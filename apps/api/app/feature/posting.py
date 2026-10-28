"""PostingService：建立、修改、作廢單據，並負責所有過帳規則（開發文件 3、5.2、7）。

不管單據從哪裡來（人工、應付表、發票、銀行、系統），都經過這裡，規則只有一套。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from sqlalchemy import func, select

from app.errors import (AmountMismatch, AssetLifeRequired, DuplicateAllowance, DuplicateInvoice, DuplicateSource,
                        HasOffsets, HasSettlements, NotFound, PeriodClosed, TaxIdRequired, ValidationFailed,
                        VersionConflict)
from app.feature.classification import classify
from app.feature.common import (CASH_IN_TYPES, PERIOD_RE, SETTLEABLE_TYPES, DocumentInput, LineInput, add_months,
                                cash_effect, due_date_for, months_in_memo, period_of)
from app.feature.models import Counterparty, Document, DocumentLine, DocumentOffset, RecurringTemplate
from app.feature.repositories import CatalogRepository, DocumentRepository, OpsRepository
from app.seams.company import get_directory

SYSTEM_SOURCES = {"system", "opening", "allocation"}


@dataclass
class Finding:
    code: str
    level: str  # ok / warn / info / error
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message, "data": self.data}


class PostingService:
    def __init__(self, session: Session):
        self.s = session
        self.catalog = CatalogRepository(session)
        self.docs = DocumentRepository(session)
        self.ops = OpsRepository(session)
        self.dir = get_directory()
        self.cfg = get_settings()

    # ------------------------------------------------------------------ 建立
    def create(self, inp: DocumentInput, actor: str, bulk: bool = False) -> Document:
        """bulk=True：不個別寫入，由呼叫端最後一次 flush（批次匯入用；重複來源要先自行排除）。"""
        cp = self._resolve_counterparty(inp, actor)
        self._validate(inp, cp)

        doc = Document(
            doc_type=inp.doc_type, source_kind=inp.source_kind, source_ref=inp.source_ref,
            import_batch_id=inp.import_batch_id, company_id=inp.company_id,
            counterparty_id=cp.id if cp else None, doc_date=inp.doc_date, invoice_nos=list(inp.invoice_nos),
            has_invoice=inp.has_invoice, tax_amount=inp.tax_amount, settle_amount=inp.settle_amount,
            due_date=inp.due_date, memo=inp.memo, accrual_mode=inp.accrual_mode,
            accrual_template_id=inp.accrual_template_id, expected_delivery_date=inp.expected_delivery_date,
            related_document_id=inp.related_document_id, relation=inp.relation, created_by=actor,
            status=inp.status, settled_amount=0, offset_amount=0,
            expected_invoice_period=inp.expected_invoice_period, planned_category_code=inp.planned_category_code,
            order_total=inp.order_total, original_invoice_no=inp.original_invoice_no,
            allowance_no=inp.allowance_no, allowance_reason=inp.allowance_reason, refund_method=inp.refund_method,
            on_behalf_of_company_id=inp.on_behalf_of_company_id, paid_by_company_id=inp.paid_by_company_id,
            invoice_buyer_tax_id=inp.invoice_buyer_tax_id,
        )
        if bulk:
            doc.id = uuid.uuid4()  # 先給 ID，flush 前就能被待確認等引用
        if inp.doc_type in SETTLEABLE_TYPES:
            if doc.due_date is None and inp.doc_type == "payable":
                doc.due_date = due_date_for(inp.doc_date, cp.payment_rule if cp else self.cfg.default_payment_rule)
            if inp.settlement_method == "petty_cash":
                doc.settle_status, doc.settled_by, doc.settled_amount = "settled", "petty_cash", inp.settle_amount or 0
            else:
                doc.settle_status = "open"
        elif inp.doc_type == "allowance":
            # 尾款少付：由 OffsetService 沖到原單後才算結清；廠商退錢／留著下次抵：等退款或沖抵
            doc.settle_status = "open"
        else:
            doc.settle_status = "not_applicable"
        if doc.status == "posted":
            doc.posted_at = datetime.now(timezone.utc)

        intercompany = bool(cp and cp.is_intercompany)
        for i, ln in enumerate(inp.lines, start=1):
            doc.lines.append(DocumentLine(line_no=i, company_id=self._line_company(inp.company_id, ln.store_id),
                                          store_id=ln.store_id,
                                          period_key=ln.period_key, category_code=ln.category_code,
                                          amount=ln.amount, description=ln.description,
                                          counterparty_id=ln.counterparty_id,
                                          original_document_id=ln.original_document_id,
                                          account_override_id=ln.account_override_id,
                                          is_intercompany=intercompany and ln.category_code in ("C99", "R06")))
        if bulk:
            self.s.add(doc)
            self._after_create(doc, inp, actor)
            return doc
        try:
            with self.s.begin_nested():
                self.docs.add(doc)
        except IntegrityError as e:
            if "documents_unique_source" in str(e.orig):
                raise DuplicateSource(f"這個來源已經入帳過（{inp.source_kind}:{inp.source_ref}）") from e
            raise

        self._after_create(doc, inp, actor)
        return doc

    def _resolve_counterparty(self, inp: DocumentInput, actor: str) -> Counterparty | None:
        if inp.counterparty_id:
            cp = self.catalog.counterparty(inp.counterparty_id)
            if cp is None:
                raise ValidationFailed("找不到這個對象", [{"field": "counterparty_id", "message": "不存在"}])
            return cp
        if inp.counterparty_name:
            cp = self.catalog.find_counterparty(inp.counterparty_name, None)
            if cp is None:
                # 從銀行交易建立的（薪資、手續費…）：對象多半是員工或銀行，不開「新對象」待確認
                cp = self.ensure_counterparty(inp.counterparty_name, None, inp.company_id, actor,
                                              roles=["payee"] if inp.source_kind == "bank_only" else None,
                                              review=inp.source_kind != "bank_only")
            return cp
        return None

    def ensure_counterparty(self, name: str, tax_id: str | None, company_id: str, actor: str,
                            roles: list[str] | None = None, review: bool = True) -> Counterparty:
        cp = self.catalog.find_counterparty(name, tax_id)
        if cp:
            return cp
        group = self.dir.get_by_tax_id(tax_id) if tax_id else None
        cp = self.catalog.add_counterparty(name=name, tax_id=tax_id, roles=roles or ["vendor"],
                                           is_intercompany=group is not None,
                                           intercompany_company_id=group.id if group else None,
                                           payment_rule=self.cfg.default_payment_rule)
        if review:
            self.ops.open_review("new_counterparty", company_id, f"counterparty:{cp.id}",
                                 {"name": name, "tax_id": tax_id, "created_by": actor})
        return cp

    def _validate(self, inp: DocumentInput, cp: Counterparty | None) -> None:
        errs: list[dict] = []
        if self.dir.get_company(inp.company_id) is None:
            errs.append({"field": "company_id", "message": "公司不存在"})
        if inp.doc_type in SETTLEABLE_TYPES and inp.settle_amount is None:
            errs.append({"field": "settle_amount", "message": "應付／應收金額必填"})
        if inp.doc_type == "accrual" and inp.accrual_mode not in ("estimate_reverse", "provision"):
            errs.append({"field": "accrual_mode", "message": "預估入帳要選模式"})
        if inp.doc_type == "prepayment":
            if cp is None:
                errs.append({"field": "counterparty_id", "message": "預付款要選廠商"})
            elif not cp.tax_id and not cp.no_tax_id and not cp.id_masked:
                raise TaxIdRequired("預付款要有廠商統編，發票來時才能比對沖抵；沒有統編請先在廠商主檔勾選「沒有統編」",
                                    [{"field": "counterparty_tax_id", "message": "必填"}])
            if any(ln.category_code != "B07" for ln in inp.lines):
                errs.append({"field": "lines", "message": "預付款只記 B07 預付款，付款時不進損益"})
        if inp.doc_type == "allowance":
            if cp is None:
                errs.append({"field": "counterparty_id", "message": "折讓要選廠商"})
            if not inp.refund_method:
                errs.append({"field": "refund_method", "message": "請選錢怎麼回來"})
            for i, ln in enumerate(inp.lines):
                if ln.amount >= 0:
                    errs.append({"field": f"lines[{i}].amount", "message": "折讓明細為負數"})
        if inp.doc_type == "advance_receipt":
            if inp.expected_delivery_date is None:
                errs.append({"field": "expected_delivery_date", "message": "預收款要填預計交貨或兌換日"})
            if inp.lines:
                errs.append({"field": "lines", "message": "預收款收款時不進損益，不能有明細"})
        elif not inp.lines:
            errs.append({"field": "lines", "message": "至少要有一行明細"})

        for i, ln in enumerate(inp.lines):
            cat = self.catalog.category(ln.category_code)
            if cat is None or not cat.is_active:
                errs.append({"field": f"lines[{i}].category_code", "message": f"類別 {ln.category_code} 不存在"})
            if not PERIOD_RE.match(ln.period_key or ""):
                errs.append({"field": f"lines[{i}].period_key", "message": "月份格式為 YYYY-MM"})
            if ln.store_id:
                st = self.dir.get_store(ln.store_id)
                if st is None or not self.dir.same_legal_entity(st.company_id, inp.company_id):
                    errs.append({"field": f"lines[{i}].store_id", "message": "門市不屬於這家公司"})
            if inp.doc_type == "accrual" and cat is not None and cat.nature == "balance":
                errs.append({"field": f"lines[{i}].category_code", "message": "預估入帳不能用 B 類"})
        if errs:
            raise ValidationFailed("單據內容不完整", errs)

        if inp.doc_type == "allowance" and inp.lines:
            # 折讓：明細與稅額都是負數，加總＝−折讓含稅金額（開發文件 7）
            total = sum(cash_effect(self.catalog.category(ln.category_code).nature, ln.amount, "payable")
                        for ln in inp.lines) + inp.tax_amount
            if inp.settle_amount is None or inp.settle_amount <= 0:
                raise ValidationFailed("折讓金額（含稅）要大於 0", [{"field": "settle_amount", "message": "必填"}])
            diff = -inp.settle_amount - total
            if abs(diff) > self.cfg.amount_tolerance:
                raise AmountMismatch(f"折讓明細合計＋稅額 = {total:,}，與折讓金額 −{inp.settle_amount:,} 差 {diff:,}",
                                     [{"field": "settle_amount", "message": f"差額 {diff}"}])
        if inp.doc_type in SETTLEABLE_TYPES and inp.lines:
            total = sum(cash_effect(self.catalog.category(ln.category_code).nature, ln.amount, inp.doc_type)
                        for ln in inp.lines) + inp.tax_amount
            diff = (inp.settle_amount or 0) - total
            if abs(diff) > self.cfg.amount_tolerance:
                raise AmountMismatch(f"明細合計＋稅額 = {total:,}，與應付／應收金額 {inp.settle_amount:,} 差 {diff:,}",
                                     [{"field": "settle_amount", "message": f"差額 {diff}"}])
        if inp.doc_type == "advance_receipt" and (inp.settle_amount or 0) <= 0:
            raise ValidationFailed("預收金額要大於 0")

        if not inp.allow_closed_period and inp.source_kind not in SYSTEM_SOURCES:
            for ln in inp.lines:
                if self.ops.is_closed(self._line_company(inp.company_id, ln.store_id), ln.period_key):
                    raise PeriodClosed(f"{ln.period_key} 已關帳。要記到 {add_months(ln.period_key, 1)}，"
                                       f"或請管理者重開 {ln.period_key}？", [{"field": "period_key",
                                                                              "message": ln.period_key}])

        if inp.doc_type == "allowance" and inp.allowance_no and cp is not None:
            from app.feature.models import Document as _D
            dup = self.s.scalar(select(_D).where(_D.doc_type == "allowance", _D.counterparty_id == cp.id,
                                                 _D.allowance_no == inp.allowance_no, _D.status != "void"))
            if dup:
                raise DuplicateAllowance(f"{cp.name} 的折讓單 {inp.allowance_no} 已經建立過",
                                         [{"field": "allowance_no", "message": str(dup.id)}])
        if inp.source_kind == "manual" and cp is not None and inp.doc_type != "allowance":
            for no in inp.invoice_nos:
                dup = self.docs.find_by_invoice(no, counterparty_id=cp.id)
                if dup:
                    raise DuplicateInvoice(f"{cp.name} 的發票 {no} 已經入帳", [{"field": "invoice_nos",
                                                                              "message": str(dup.id)}])

        capex = sum(ln.amount for ln in inp.lines if ln.category_code == "B01")
        if capex >= self.cfg.capex_threshold and not inp.asset_life_months and inp.source_kind != "opening":
            raise AssetLifeRequired("資本支出超過門檻，請填耐用年限（月）")

    def _after_create(self, doc: Document, inp: DocumentInput, actor: str) -> None:
        # 資本支出：產生折舊範本（B01 −x、O05 +x），最後一期吸收尾差
        if inp.asset_life_months:
            for ln in doc.lines:
                if ln.category_code != "B01" or ln.amount <= 0:
                    continue
                n = inp.asset_life_months
                base = ln.amount // n
                self.ops.add(RecurringTemplate(
                    company_id=doc.company_id, store_id=ln.store_id, category_code="O05", doc_type="journal",
                    offset_category_code="B01", related_document_id=doc.id, amount=base,
                    last_amount=ln.amount - base * (n - 1), start_period=ln.period_key,
                    end_period=add_months(ln.period_key, n - 1),
                    description=f"折舊：{ln.description or doc.memo or '設備'}（{n} 個月）"))
        if doc.status == "posted" and any(ln.category_code == "B99" for ln in doc.lines):
            self.ops.open_review("b99_open", doc.company_id, f"document:{doc.id}", {"document_id": str(doc.id)},
                                 period_key=doc.lines[0].period_key)
        # 應付入帳：同一法人、同賣方統編有未沖抵的預付 → 待確認「待沖抵預付」（5.13；第一階段一律人工確認）
        if (doc.doc_type == "payable" and inp.check_prepayments and doc.counterparty_id is not None
                and doc.counterparty_id in self._prepay_vendors()):
            from app.feature.offsets import OffsetService
            cands = OffsetService(self.s).open_prepayments(doc.company_id, doc.counterparty_id, before=doc.doc_date)
            if cands:
                cp = self.catalog.counterparty(doc.counterparty_id)
                self.ops.open_review(
                    "prepayment_match", doc.company_id, f"document:{doc.id}",
                    {"document_id": str(doc.id), "counterparty_name": cp.name if cp else None,
                     "tax_id": cp.tax_id if cp else None, "invoice_nos": list(doc.invoice_nos),
                     "settle_amount": doc.settle_amount, "doc_date": doc.doc_date.isoformat(),
                     "candidates": cands},
                    period_key=doc.lines[0].period_key if doc.lines else None)

    def _prepay_vendors(self) -> set:
        """有未沖抵預付的廠商（快取：批次匯入幾百列時只查一次）。"""
        if not hasattr(self, "_pv"):
            from app.feature.offsets import OffsetService
            self._pv = OffsetService(self.s).vendors_with_open_prepayments()
        return self._pv

    # ------------------------------------------------------------------ 檢查（登打中）
    def check(self, inp: DocumentInput) -> list[Finding]:
        out: list[Finding] = []
        cp = (self.catalog.counterparty(inp.counterparty_id) if inp.counterparty_id
              else self.catalog.find_counterparty(inp.counterparty_name, None) if inp.counterparty_name else None)
        if cp is not None:
            dup = next((d for no in inp.invoice_nos if (d := self.docs.find_by_invoice(no, counterparty_id=cp.id))),
                       None)
            out.append(Finding("duplicate", "error", f"{cp.name} 的這張發票已經入帳", {"document_id": str(dup.id)})
                       if dup else Finding("duplicate", "ok", "沒有重複"))
            if cp.is_intercompany:
                out.append(Finding("intercompany", "info", "對象是集團內公司"))
        months = months_in_memo(inp.memo, period_of(inp.doc_date))
        if months and len(months) > 1 and len({ln.period_key for ln in inp.lines}) < len(months):
            out.append(Finding("split_months", "warn", f"摘要含多個月份，建議拆成 {len(months)} 個月",
                               {"periods": months}))
        closed = sorted({ln.period_key for ln in inp.lines if self.ops.is_closed(inp.company_id, ln.period_key)})
        out.append(Finding("period_closed", "error", f"{'、'.join(closed)} 已關帳") if closed
                   else Finding("period_closed", "ok", "月份都還沒關帳"))
        if not inp.lines and inp.doc_type != "advance_receipt":
            c = classify(self.catalog, cp, inp.memo, "income" if inp.doc_type in CASH_IN_TYPES else "expense",
                         inp.settle_amount or 0)
            if c.decided:
                out.append(Finding("category", "info", f"建議類別 {c.category_code}（{c.layer}）",
                                   {"category_code": c.category_code, "layer": c.layer}))
            elif c.suggestion:
                out.append(Finding("category", "info", "AI 建議（採用前不會入帳）", c.suggestion))
        if any(self.catalog.category(ln.category_code) and self.catalog.category(ln.category_code).nature == "balance"
               for ln in inp.lines):
            out.append(Finding("balance_line", "info", "含 B 類明細，這部分不進損益"))
        if not inp.has_invoice and inp.doc_type == "payable":
            out.append(Finding("no_invoice", "info", "無發票：金額即成本，進項稅額不能扣抵"))
        return out

    # ------------------------------------------------------------------ 修改
    def update(self, doc_id: uuid.UUID, expected_version: int, actor: str, *, memo: str | None = None,
               due_date=None, lines: list[LineInput] | None = None, settle_amount: int | None = None,
               tax_amount: int | None = None) -> Document:
        doc = self.docs.get(doc_id, lock=True)
        if doc is None or doc.status == "void":
            raise NotFound("單據不存在或已作廢")
        if doc.version != expected_version:
            raise VersionConflict("這筆剛被別人修改，已重新載入")
        if memo is not None:
            doc.memo = memo
        if due_date is not None:
            doc.due_date = due_date
        if lines is not None or settle_amount is not None or tax_amount is not None:
            if doc.settled_amount and (settle_amount is not None and settle_amount != doc.settle_amount):
                raise HasSettlements("這筆已有付款紀錄，不能改金額")
            new_lines = lines if lines is not None else [LineInput(l.period_key, l.category_code, l.amount,
                                                                   l.store_id, l.description) for l in doc.lines]
            for p in {l.period_key for l in doc.lines} | {l.period_key for l in new_lines}:
                if self.ops.is_closed(doc.company_id, p):
                    raise PeriodClosed(f"{p} 已關帳，不能修改")
            inp = DocumentInput(doc_type=doc.doc_type, company_id=doc.company_id, doc_date=doc.doc_date,
                                lines=new_lines, source_kind="system", counterparty_id=doc.counterparty_id,
                                settle_amount=settle_amount if settle_amount is not None else doc.settle_amount,
                                tax_amount=tax_amount if tax_amount is not None else doc.tax_amount,
                                accrual_mode=doc.accrual_mode, expected_delivery_date=doc.expected_delivery_date,
                                asset_life_months=1 if any(l.category_code == "B01" for l in new_lines) else None)
            self._validate(inp, doc.counterparty)
            doc.settle_amount, doc.tax_amount = inp.settle_amount, inp.tax_amount
            doc.lines.clear()
            self.s.flush()
            for i, ln in enumerate(new_lines, start=1):
                doc.lines.append(DocumentLine(line_no=i, company_id=self._line_company(doc.company_id, ln.store_id),
                                              store_id=ln.store_id,
                                              period_key=ln.period_key, category_code=ln.category_code,
                                              amount=ln.amount, description=ln.description,
                                              is_intercompany=bool(doc.counterparty and doc.counterparty.is_intercompany
                                                                   and ln.category_code in ("C99", "R06"))))
            self.recompute_settlement(doc)
        doc.version += 1
        doc.updated_at = datetime.now(timezone.utc)
        self.s.flush()
        return doc

    def post(self, doc: Document) -> None:
        if doc.status == "draft":
            doc.status, doc.posted_at = "posted", datetime.now(timezone.utc)
            doc.version += 1
            doc.updated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ 作廢
    def void(self, doc_id: uuid.UUID, reason: str, actor: str, *, force: bool = False) -> Document:
        doc = self.docs.get(doc_id, lock=True)
        if doc is None:
            raise NotFound("單據不存在")
        if doc.status == "void":
            return doc
        if not reason:
            raise ValidationFailed("作廢需填原因")
        if any(st.reversed_at is None for st in doc.settlements) and not force:
            raise HasSettlements("這筆已核對到銀行付款，請先在現金流系統取消配對")
        if not force and self.s.scalar(select(func.count()).select_from(DocumentOffset).where(
                DocumentOffset.reversed_at.is_(None),
                (DocumentOffset.from_document_id == doc.id) | (DocumentOffset.to_document_id == doc.id))):
            raise HasOffsets("這筆已沖抵了預付、折讓或代付，請先取消沖抵")
        for p in {l.period_key for l in doc.lines}:
            if self.ops.is_closed(doc.company_id, p) and not force:
                raise PeriodClosed(f"{p} 已關帳，不能作廢")
        doc.status, doc.void_reason = "void", reason
        doc.version += 1
        doc.updated_at = datetime.now(timezone.utc)
        for t in self.s.query(RecurringTemplate).filter(RecurringTemplate.related_document_id == doc.id):
            t.is_active = False
        self.s.flush()
        return doc

    # ------------------------------------------------------------------ 結清狀態
    def _line_company(self, doc_company: str, store_id: str | None) -> str:
        """明細算哪家公司的損益：有門市就算門市所屬公司（例：發票開總公司統編、巨蛋店的貨 → 高雄分公司），
        沒有門市就算單據的公司。門市一定在同一法人內（_validate 已檢查）。"""
        if store_id:
            st = self.dir.get_store(store_id)
            if st is not None and self.dir.same_legal_entity(st.company_id, doc_company):
                return st.company_id
        return doc_company

    def recompute_settlement(self, doc: Document) -> None:
        """結清狀態＝銀行／零用金已付（settlements）＋沖抵（document_offsets，見 3.8）對上結清金額。"""
        if doc.doc_type not in SETTLEABLE_TYPES | {"allowance"} or doc.settled_by == "petty_cash":
            return
        paid = sum(st.amount for st in doc.settlements if st.reversed_at is None)
        offset = doc.offset_amount or 0
        if doc.doc_type == "allowance":  # 折讓是「沖人的一方」：用掉的部分＝沖到其他單據的金額
            offset = self.s.scalar(select(func.coalesce(func.sum(DocumentOffset.amount), 0)).where(
                DocumentOffset.from_document_id == doc.id, DocumentOffset.reversed_at.is_(None))) or 0
        target = doc.settle_amount or 0
        doc.settled_amount = paid
        doc.settled_by = ("bank" if paid else
                          "paid_by_other" if offset and doc.paid_by_company_id else "offset" if offset else None)
        paid += offset
        if paid == 0:
            doc.settle_status = "open"
        elif paid < target:
            doc.settle_status = "partial"
        elif paid == target:
            doc.settle_status = "settled"
        else:
            doc.settle_status = "over"
        doc.updated_at = datetime.now(timezone.utc)
