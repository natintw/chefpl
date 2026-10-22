"""匯入流程（開發文件 5.3）：上傳 → 解析預覽（不入帳）→ 確認 commit → 建立單據。

同一公司、同一月份、同一種匯入再 commit 一次：舊批次的單據作廢、批次標為 superseded（已有付款的單據不能被取代）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import HasErrors, NotFound, ValidationFailed
from app.feature.classification import classify
from app.feature.common import (PERIOD_RE, DocumentInput, LineInput, last_day, months_in_memo, split_evenly,
                                untaxed)
from app.feature.imports.parsers import PARSERS, ParsedRow
from app.feature.models import ImportBatch
from app.feature.posting import PostingService
from app.feature.review import _input_from_json, input_to_json
from app.seams.company import get_directory
from app.seams.files import get_file_store

PAYROLL_COUNTERPARTY = "員工薪資"


class ImportService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.ops, self.catalog = self.posting.docs, self.posting.ops, self.posting.catalog
        self.dir = get_directory()
        self.cfg = get_settings()

    # ------------------------------------------------------------------ 上傳＋解析
    def upload(self, *, kind: str, company_id: str, period: str, store_id: str | None, file_name: str,
               data: bytes, actor: str) -> ImportBatch:
        if kind not in PARSERS:
            raise ValidationFailed(f"不支援的匯入類型：{kind}")
        if not PERIOD_RE.match(period):
            raise ValidationFailed("月份格式為 YYYY-MM")
        if self.dir.get_company(company_id) is None:
            raise ValidationFailed("公司不存在")
        if len(data) > self.cfg.max_upload_mb * 1024 * 1024:
            raise ValidationFailed(f"檔案超過 {self.cfg.max_upload_mb} MB")
        key = get_file_store().put("imports/", file_name, data,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        try:
            result = PARSERS[kind](data)
        except Exception as e:  # noqa: BLE001
            result = None
            err = [{"row": 0, "code": "UNREADABLE", "message": f"這個檔案不是支援的格式（{type(e).__name__}）"}]
        batch = ImportBatch(kind=kind, company_id=company_id, period_key=period, file_name=file_name, file_url=key,
                            status="parsed", imported_by=actor)
        if result is None:
            batch.status, batch.errors, batch.parsed, batch.warnings = "failed", err, [], []
            return self.ops.add_batch(batch)

        self.ops.add_batch(batch)
        builder = {"ap_sheet": self._ap_row, "payroll": self._payroll_row}[kind]
        parsed, errors, warnings = [], list(result.errors), list(result.warnings)
        for r in result.rows:
            try:
                inp, extra = builder(r, batch, store_id, actor)
            except ValidationFailed as e:
                errors.append({"row": r.row, "code": e.code, "message": e.message})
                continue
            parsed.append({"row": r.row, "input": input_to_json(inp), **extra})
            warnings.extend({"row": r.row, **w} for w in extra.get("warnings", []))
        batch.parsed, batch.errors, batch.warnings = parsed, errors, warnings
        batch.row_count = len(parsed)
        batch.total_amount = sum(p["input"]["settle_amount"] for p in parsed)
        return batch

    def _store(self, name: str | None, company_id: str, default: str | None) -> str | None:
        if not name:
            return default
        for st in self.dir.list_stores():
            if self.dir.same_legal_entity(st.company_id, company_id) and name in (st.name, st.code, st.id):
                return st.id
        raise ValidationFailed(f"找不到門市「{name}」")

    def _ap_row(self, r: ParsedRow, batch: ImportBatch, store_id: str | None, actor: str):
        v = r.values
        period = batch.period_key
        extra_w: list[dict] = []
        cp = self.catalog.find_counterparty(v["vendor"], v.get("tax_id"))
        if cp is None:
            cp = self.posting.ensure_counterparty(v["vendor"], v.get("tax_id"), batch.company_id, actor)
            if v.get("id_masked"):
                cp.id_masked = v["id_masked"]
            extra_w.append({"code": "NEW_COUNTERPARTY", "message": f"新廠商「{v['vendor']}」，已建立並列入待確認"})
        if cp.is_intercompany:
            extra_w.append({"code": "INTERCOMPANY", "message": "集團內交易，月結時需與對方公司核對"})

        if v.get("category"):
            code, decided, suggestion = v["category"], True, None
            if self.catalog.category(code) is None:
                raise ValidationFailed(f"類別代碼 {code} 不存在")
        else:
            c = classify(self.catalog, cp, v.get("memo"), "expense", v["amount"])
            code, decided, suggestion = c.category_code, c.decided, c.suggestion
            if not decided:
                extra_w.append({"code": "UNCLASSIFIED", "message": "分類規則沒有命中，入帳後進待確認"})
        if decided and v.get("kind"):
            sec = self.catalog.category(code).pl_section
            if (v["kind"] == "cost") != (sec == "cogs") and sec != "balance":
                extra_w.append({"code": "KIND_CONFLICT", "message": f"原表為{'成本' if v['kind'] == 'cost' else '費用'}，"
                                                                   f"但類別 {code} 屬於另一類；以類別為準"})

        has_inv = v.get("has_invoice", False)
        net = untaxed(v["amount"], has_inv, self.cfg.tax_rate)
        tax = v["amount"] - net
        doc_date = date.fromisoformat(v["date"]) if v.get("date") else last_day(period)
        months = months_in_memo(v.get("memo"), period) or [period]
        if len(months) > 1:
            extra_w.append({"code": "MULTI_MONTH", "message": f"跨月費用，已拆成 {'、'.join(months)}"})
        store = self._store(v.get("store"), batch.company_id, store_id)
        lines = [LineInput(m, code or "B99", amt, store, v.get("memo"))
                 for m, amt in zip(months, split_evenly(net, len(months)))]
        inp = DocumentInput(
            doc_type="payable", company_id=batch.company_id, doc_date=doc_date, source_kind="ap_sheet",
            source_ref=f"{batch.id}:{r.row}", counterparty_id=cp.id, invoice_nos=v.get("invoice_nos", []),
            has_invoice=has_inv, tax_amount=tax, settle_amount=v["amount"], memo=v.get("memo"),
            import_batch_id=batch.id, status="posted" if decided else "draft", lines=lines)
        return inp, {"warnings": extra_w, "suggestion": suggestion, "vendor": v["vendor"]}

    def _payroll_row(self, r: ParsedRow, batch: ImportBatch, store_id: str | None, actor: str):
        v = r.values
        period = batch.period_key
        store = None if v["store"] in ("公司層級", "總部", "辦公室") else self._store(v["store"], batch.company_id, None)
        cp = self.posting.ensure_counterparty(PAYROLL_COUNTERPARTY, None, batch.company_id, actor, roles=["vendor"])
        cp.payment_rule = "next_month_5"
        parts = [("P01", v.get("P01", 0), "薪資"), ("P02", v.get("P02", 0), "加班與獎金"),
                 ("P03", v.get("P03_labor", 0) + v.get("P03_health", 0), "勞健保雇主負擔"),
                 ("P04", v.get("P04", 0), "勞退提繳")]
        lines = [LineInput(period, code, amt, store, label) for code, amt, label in parts if amt]
        total = sum(l.amount for l in lines)
        inp = DocumentInput(
            doc_type="payable", company_id=batch.company_id, doc_date=last_day(period), source_kind="payroll",
            source_ref=f"{batch.company_id}:{store or 'company'}:{period}", counterparty_id=cp.id,
            settle_amount=total,  # 到期日依「員工薪資」的付款條件：次月 5 日
            memo=f"{period} 薪資（{v['store']}）", import_batch_id=batch.id, lines=lines)
        return inp, {"warnings": []}

    # ------------------------------------------------------------------ 確認入帳
    def commit(self, batch_id: uuid.UUID, actor: str) -> dict[str, Any]:
        batch = self.ops.batch(batch_id)
        if batch is None:
            raise NotFound("找不到匯入批次")
        if batch.status != "parsed":
            raise ValidationFailed(f"這個批次狀態為 {batch.status}，不能再入帳")
        if batch.errors:
            raise HasErrors(f"有 {len(batch.errors)} 筆錯誤，請修正後重新上傳", batch.errors[:20])

        superseded = []
        for old in self.ops.committed_batches(batch.kind, batch.company_id, batch.period_key):
            old_docs, _ = self.docs.list(company_ids={batch.company_id}, page_size=10000)
            mine = [d for d in old_docs if d.import_batch_id == old.id]
            paid = [d for d in mine if d.settled_amount]
            if paid:
                raise HasErrors(f"舊批次有 {len(paid)} 筆已付款，不能整批取代；請改用個別修改",
                                [{"row": 0, "message": str(d.id)} for d in paid[:20]])
            for d in mine:
                self.posting.void(d.id, f"由批次 {batch.id} 取代", actor)
            old.status, old.superseded_by = "superseded", batch.id
            superseded.append(str(old.id))
        if batch.kind == "payroll":
            # 薪資的來源鍵是「公司＋門市＋月份」：同月重新匯入時，作廢舊的那張
            for p in batch.parsed:
                prev = self.docs.get_by_source("payroll", p["input"]["source_ref"])
                if prev is not None and prev.import_batch_id != batch.id:
                    self.posting.void(prev.id, f"由批次 {batch.id} 取代", actor)

        created, drafts = 0, 0
        for p in batch.parsed:
            inp = _input_from_json(p["input"])
            doc = self.posting.create(inp, actor)
            created += 1
            if doc.status == "draft":
                drafts += 1
                self.ops.open_review("unclassified", doc.company_id, f"document:{doc.id}",
                                     {"document_id": str(doc.id), "counterparty": p.get("vendor"),
                                      "suggestion": p.get("suggestion")}, period_key=batch.period_key)
        batch.status, batch.committed_at = "committed", datetime.now(timezone.utc)
        return {"batch_id": str(batch.id), "created": created, "drafts_for_review": drafts,
                "total_amount": batch.total_amount, "superseded_batches": superseded}

    def serialize(self, b: ImportBatch) -> dict[str, Any]:
        return {"id": str(b.id), "kind": b.kind, "company_id": b.company_id, "period": b.period_key,
                "file_name": b.file_name, "status": b.status, "row_count": b.row_count,
                "total_amount": b.total_amount, "warnings": b.warnings, "errors": b.errors,
                "preview": [{"row": p["row"], "counterparty": p.get("vendor"),
                             "settle_amount": p["input"]["settle_amount"], "lines": p["input"]["lines"],
                             "status": p["input"].get("status")} for p in b.parsed[:200]]}
