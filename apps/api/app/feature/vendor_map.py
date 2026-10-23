"""廠商類別對應表：廠商（以統一編號為準）→ 管理類別（mgmt_categories.code）。

發票系統只給廠商與金額，沒有科目，所以靠這張表決定每張發票進損益表的哪一行。
實際存在 counterparties.default_category_code，分類引擎的「廠商預設」層會用它（見 classification.py）。

- 以統一編號比對；個人廠商用遮蔽後的身分證字號＋姓名；都沒有才用名稱（含別名）。
- 設定或修改類別時，這個廠商「等待分類」的草稿會一起入帳，待確認同步關掉。
- 匯入檔可以順便修正廠商名稱：統編相同、名稱不同時，以檔案的名稱為準，舊名稱留作別名。
"""
from __future__ import annotations

import io
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.errors import NotFound, ValidationFailed
from app.feature.imports.parsers import ID_NUMBER_RE, TAX_ID_RE, _find_header, _norm, mask_id_number
from app.feature.models import Counterparty, Document, ReviewItem
from app.feature.repositories import CatalogRepository, OpsRepository

MASKED_RE = re.compile(r"^[A-Z][12]\*{5}\d{3}$")
ALIASES = {
    "tax_id": ["統一編號", "廠商統編", "統編"],
    "name": ["廠商名稱", "廠商", "名稱"],
    "category": ["類別代碼", "類別"],
}
VENDOR_NATURES = {"expense", "balance"}


@dataclass
class ImportResult:
    updated: int = 0
    created: int = 0
    unchanged: int = 0
    skipped_blank: int = 0
    applied_drafts: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class VendorMapService:
    def __init__(self, session: Session):
        self.s = session
        self.catalog = CatalogRepository(session)
        self.ops = OpsRepository(session)

    # ------------------------------------------------------------------ 查詢
    def list(self, q: str | None = None, only_unmapped: bool = False) -> list[dict[str, Any]]:
        stats = {row.counterparty_id: row for row in self.s.execute(
            select(Document.counterparty_id, func.count().label("n"),
                   func.coalesce(func.sum(Document.settle_amount), 0).label("amount"),
                   func.sum(case((Document.status == "draft", 1), else_=0)).label("drafts"))
            .where(Document.status != "void", Document.doc_type == "payable")
            .group_by(Document.counterparty_id))}
        out = []
        for cp in self.s.scalars(select(Counterparty).where(Counterparty.is_active.is_(True))):
            if "vendor" not in (cp.roles or ["vendor"]):
                continue
            if only_unmapped and cp.default_category_code:
                continue
            if q and q not in cp.name and q not in (cp.tax_id or "") and not any(q in a for a in cp.aliases or []):
                continue
            st = stats.get(cp.id)
            cat = self.catalog.category(cp.default_category_code) if cp.default_category_code else None
            out.append({"id": str(cp.id), "name": cp.name, "tax_id": cp.tax_id, "id_masked": cp.id_masked,
                        "aliases": cp.aliases or [], "category_code": cp.default_category_code,
                        "category_name": cat.name if cat else None, "is_intercompany": cp.is_intercompany,
                        "doc_count": int(st.n) if st else 0, "amount": int(st.amount) if st else 0,
                        "drafts": int(st.drafts or 0) if st else 0})
        out.sort(key=lambda r: (r["category_code"] is not None, -r["amount"], r["name"]))
        return out

    # ------------------------------------------------------------------ 單筆設定
    def set(self, cp_id: uuid.UUID, category_code: str | None, actor: str, name: str | None = None) -> dict[str, Any]:
        cp = self.catalog.counterparty(cp_id)
        if cp is None:
            raise NotFound("廠商不存在")
        if category_code:
            self._check_category(category_code)
        if name and name.strip() and name.strip() != cp.name:
            self._rename(cp, name.strip())
        cp.default_category_code = category_code or None
        applied = self._apply_to_drafts(cp, actor) if category_code else 0
        self._close_new_vendor_review(cp, actor)
        return {"id": str(cp.id), "name": cp.name, "category_code": cp.default_category_code, "applied_drafts": applied}

    # ------------------------------------------------------------------ Excel 匯入／匯出
    def import_xlsx(self, data: bytes, actor: str) -> ImportResult:
        try:
            ws = load_workbook(io.BytesIO(data), data_only=True, read_only=True).worksheets[0]
        except Exception as e:  # noqa: BLE001
            raise ValidationFailed(f"讀不到這個檔案（{type(e).__name__}），請上傳 .xlsx") from e
        header_row, col = _find_header(ws, ALIASES, ["category"])
        if header_row is None or ("tax_id" not in col and "name" not in col):
            raise ValidationFailed("找不到「類別代碼」欄，以及「統一編號」或「廠商名稱」欄")
        res = ImportResult()
        for r_idx, raw in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            get = lambda k: raw[col[k]] if k in col and col[k] < len(raw) else None  # noqa: E731
            name = str(get("name") or "").strip()
            code_raw = _norm(get("tax_id")).upper()
            category = _norm(get("category")).upper()
            if not name and not code_raw:
                continue
            tax_id = code_raw if TAX_ID_RE.match(code_raw) else None
            masked = mask_id_number(code_raw) if ID_NUMBER_RE.match(code_raw) else (
                code_raw if MASKED_RE.match(code_raw) else None)
            cp = self._find(tax_id, masked, name)
            if not category:  # 類別空白：不改類別，但統編對得到時仍可順便更正名稱
                if cp is not None and name and (tax_id or masked) and name != cp.name:
                    self._rename(cp, name)
                res.skipped_blank += 1
                continue
            try:
                self._check_category(category)
            except ValidationFailed as e:
                res.errors.append({"row": r_idx, "message": e.message})
                continue
            if cp is None:
                if not name:
                    res.errors.append({"row": r_idx, "message": f"找不到統編 {code_raw} 的廠商，新增時要填廠商名稱"})
                    continue
                cp = self.catalog.add_counterparty(name=name, tax_id=tax_id, id_masked=masked, roles=["vendor"],
                                                   default_category_code=category, aliases=[])
                res.created += 1
                continue
            if name and (tax_id or masked) and name != cp.name:
                self._rename(cp, name)
            if cp.default_category_code == category:
                res.unchanged += 1
            else:
                cp.default_category_code = category
                res.updated += 1
            res.applied_drafts += self._apply_to_drafts(cp, actor)
            self._close_new_vendor_review(cp, actor)
        self.s.flush()
        return res

    def export_xlsx(self) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "廠商類別"
        head = ["統一編號", "廠商名稱", "類別代碼", "類別名稱", "其他寫法（發票辨識）", "張數", "累計含稅金額"]
        ws.append(head)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="E8EEF6")
        for r in self.list():
            ws.append([r["tax_id"] or r["id_masked"] or "", r["name"], r["category_code"] or "", r["category_name"] or "",
                       "、".join(r["aliases"]), r["doc_count"], r["amount"]])
        for letter, width in zip("ABCDEFG", (12, 34, 10, 16, 40, 8, 14)):
            ws.column_dimensions[letter].width = width
        ws.freeze_panes = "A2"
        cats = [c for c in self.catalog.categories.values() if c.nature in VENDOR_NATURES and c.is_active]
        ref = wb.create_sheet("類別代碼表")
        ref.append(["類別代碼", "類別名稱", "損益表區塊", "說明"])
        for c in ref[1]:
            c.font = Font(bold=True)
        for c in cats:
            ref.append([c.code, c.name, c.pl_section, "不進損益，只記餘額" if c.nature == "balance" else ""])
        ref.column_dimensions["B"].width = 18
        dv = DataValidation(type="list", formula1=f"='類別代碼表'!$A$2:$A${len(cats) + 1}", allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"C2:C{max(ws.max_row, 2) + 200}")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # ------------------------------------------------------------------ 內部
    def _check_category(self, code: str) -> None:
        cat = self.catalog.category(code)
        if cat is None:
            raise ValidationFailed(f"類別代碼 {code} 不存在")
        if cat.nature not in VENDOR_NATURES:
            raise ValidationFailed(f"{code} {cat.name} 是收入類別，廠商（應付）不能用")

    def _find(self, tax_id: str | None, masked: str | None, name: str) -> Counterparty | None:
        if tax_id:
            return self.catalog.counterparty_by_tax_id(tax_id)
        if masked:
            return self.catalog.counterparty_by_masked(masked, name or None)
        hit = self.catalog.counterparty_by_name(name)
        if hit:
            return hit
        return self.s.scalars(select(Counterparty).where(Counterparty.aliases.any(name))).first()

    def _rename(self, cp: Counterparty, new_name: str) -> None:
        old = cp.name
        cp.name = new_name
        cp.aliases = [a for a in {*(cp.aliases or []), old} if a != new_name]

    def _apply_to_drafts(self, cp: Counterparty, actor: str) -> int:
        """這個廠商等待分類的草稿：套用類別並入帳，對應的待確認一併結案。"""
        from app.feature.review import ReviewService
        if not cp.default_category_code:
            return 0
        drafts = {d.id for d in self.s.scalars(select(Document).where(
            Document.counterparty_id == cp.id, Document.status == "draft"))}
        if not drafts:
            return 0
        review = ReviewService(self.s)
        done = 0
        items = self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "unclassified", ReviewItem.status == "open"))
        for it in items:
            doc_id = (it.payload or {}).get("document_id")
            if doc_id and uuid.UUID(doc_id) in drafts:
                review.resolve(it.id, "set_category",
                               {"category_code": cp.default_category_code, "remember_rule": False}, actor)
                drafts.discard(uuid.UUID(doc_id))
                done += 1
        return done

    def _close_new_vendor_review(self, cp: Counterparty, actor: str) -> None:
        for it in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "new_counterparty",
                                                          ReviewItem.status == "open",
                                                          ReviewItem.ref == f"counterparty:{cp.id}")):
            it.status, it.resolved_by = "resolved", actor
            it.resolution = {"action": "vendor_map", "category_code": cp.default_category_code}
            it.resolved_at = datetime.now(timezone.utc)
