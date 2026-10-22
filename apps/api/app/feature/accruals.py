"""預估入帳、預提、預收轉營收、固定費用（開發文件 5.5、5.9、5.10）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ExceedsAdvance, NotFound, PeriodClosed, ValidationFailed
from app.feature.common import DocumentInput, LineInput, add_months, day_in_period, last_day, untaxed
from app.feature.models import Document
from app.feature.posting import PostingService


@dataclass
class AccrualSuggestion:
    template_id: uuid.UUID
    company_id: str
    store_id: str | None
    category_code: str
    counterparty_id: uuid.UUID | None
    description: str
    suggested_amount: int
    basis: dict[str, int]

    def to_dict(self) -> dict:
        return {"template_id": str(self.template_id), "company_id": self.company_id, "store_id": self.store_id,
                "category_code": self.category_code,
                "counterparty_id": str(self.counterparty_id) if self.counterparty_id else None,
                "description": self.description, "suggested_amount": self.suggested_amount, "basis": self.basis}


class AccrualService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.ops, self.catalog = self.posting.docs, self.posting.ops, self.posting.catalog
        self.cfg = get_settings()

    # ------------------------------------------------------------------ (a) 預估後沖回
    def suggestions(self, company_id: str, period: str) -> list[AccrualSuggestion]:
        """本月應有、但還沒有實際單據也沒有預估的費用。"""
        out = []
        n = self.cfg.accrual_lookback_months
        for t in self.ops.accrual_templates_for(period, company_id, mode="estimate_reverse"):
            has_actual = self.docs.has_lines(company_id=company_id, period=period, store_id=t.store_id,
                                             category_code=t.category_code, counterparty_id=t.counterparty_id,
                                             exclude_doc_types=("accrual",))
            if has_actual or self.docs.get_by_source("accrual", f"{t.id}:{period}"):
                continue
            hist = self.docs.sum_lines(company_id=company_id, periods=[add_months(period, -i) for i in range(1, n + 1)],
                                       category_code=t.category_code, store_id=t.store_id,
                                       counterparty_id=t.counterparty_id)
            amount = t.fixed_amount if t.estimate_method == "fixed" else (round(sum(hist.values()) / len(hist)) if hist else 0)
            out.append(AccrualSuggestion(t.id, company_id, t.store_id, t.category_code, t.counterparty_id,
                                         t.description, amount or 0, hist))
        return out

    def create_estimate(self, template_id: uuid.UUID, period: str, amount: int, actor: str) -> Document:
        t = self.ops.accrual_template(template_id)
        if t is None:
            raise NotFound("找不到預估範本")
        return self.posting.create(DocumentInput(
            doc_type="accrual", company_id=t.company_id, doc_date=last_day(period), source_kind="accrual",
            source_ref=f"{t.id}:{period}", counterparty_id=t.counterparty_id, accrual_mode=t.accrual_mode,
            accrual_template_id=t.id, memo=f"預估：{t.description}",
            lines=[LineInput(period, t.category_code, amount, t.store_id, f"預估 {period}")]), actor)

    # ------------------------------------------------------------------ 實際單據對應預估或預提
    def link(self, actual_id: uuid.UUID, accrual_id: uuid.UUID, relation: str, actor: str) -> Document | None:
        actual = self.docs.get(actual_id)
        accrual = self.docs.get(accrual_id)
        if actual is None or accrual is None or accrual.doc_type != "accrual" or accrual.status != "posted":
            raise NotFound("找不到實際單據或預估入帳")
        if actual.company_id != accrual.company_id:
            raise ValidationFailed("兩張單據不屬於同一家公司")
        if relation == "reverses_accrual":
            return self._reverse(actual, accrual, actor)
        if relation == "consumes_provision":
            return self._consume(actual, accrual, actor)
        raise ValidationFailed("relation 只能是 reverses_accrual 或 consumes_provision")

    def _reverse(self, actual: Document, accrual: Document, actor: str) -> Document | None:
        if self.docs.related(accrual.id, "reverses_accrual"):
            raise ValidationFailed("這張預估已經沖回過")
        acc_periods = {l.period_key for l in accrual.lines}
        act_periods = {l.period_key for l in actual.lines}
        # 實際帳單在同月、關帳前就到：直接作廢預估，不產生沖回
        if act_periods <= acc_periods and not any(self.ops.is_closed(accrual.company_id, p) for p in acc_periods):
            self.posting.void(accrual.id, f"實際單據 {actual.id} 已到，取代預估", actor)
            return None
        period = min(act_periods)
        if self.ops.is_closed(actual.company_id, period):
            raise PeriodClosed(f"{period} 已關帳，無法沖回")
        return self.posting.create(DocumentInput(
            doc_type="journal", company_id=accrual.company_id, doc_date=actual.doc_date, source_kind="system",
            source_ref=f"reverse:{accrual.id}", counterparty_id=accrual.counterparty_id,
            related_document_id=accrual.id, relation="reverses_accrual",
            memo=f"沖回預估（實際單據 {actual.id}）",
            lines=[LineInput(period, l.category_code, -l.amount, l.store_id, f"沖回 {l.period_key} 預估")
                   for l in accrual.lines]), actor)

    def provision_balance(self, template_id: uuid.UUID) -> int:
        docs = self.docs.provision_documents(template_id)
        accrued = sum(l.amount for d in docs if d.doc_type == "accrual" for l in d.lines)
        consumed = sum(-l.amount for d in docs if d.relation == "consumes_provision" for l in d.lines)
        return accrued - consumed

    def _consume(self, actual: Document, accrual: Document, actor: str) -> Document | None:
        if accrual.accrual_mode != "provision" or accrual.accrual_template_id is None:
            raise ValidationFailed("這張不是分月預提")
        t = self.ops.accrual_template(accrual.accrual_template_id)
        cat_lines = [l for l in actual.lines if l.category_code == t.category_code]
        if not cat_lines:
            raise ValidationFailed(f"實際單據沒有 {t.category_code} 的明細")
        use = min(self.provision_balance(t.id), sum(l.amount for l in cat_lines))
        if use <= 0:
            return None
        period = min(l.period_key for l in cat_lines)
        doc = self.posting.create(DocumentInput(
            doc_type="journal", company_id=t.company_id, doc_date=actual.doc_date, source_kind="system",
            source_ref=f"consume:{actual.id}:{t.id}", related_document_id=actual.id, relation="consumes_provision",
            accrual_template_id=t.id, memo=f"用掉預提：{t.description}",
            lines=[LineInput(period, t.category_code, -use, t.store_id, "沖減預提")]), actor)
        return doc

    # ------------------------------------------------------------------ 每月：固定費用、折舊、預提
    def run_recurring(self, period: str, actor: str = "system:worker") -> dict[str, int]:
        created = skipped = 0
        for t in self.ops.recurring_for(period):
            ref = f"{t.id}:{period}"
            if self.docs.get_by_source("recurring", ref):
                skipped += 1
                continue
            if self.ops.is_closed(t.company_id, period):
                skipped += 1
                continue
            amount = t.last_amount if (t.end_period == period and t.last_amount is not None) else t.amount
            if t.doc_type == "journal":
                lines = [LineInput(period, t.category_code, amount, t.store_id, t.description)]
                if t.offset_category_code:
                    lines.append(LineInput(period, t.offset_category_code, -amount, t.store_id, t.description))
                inp = DocumentInput(doc_type="journal", company_id=t.company_id, doc_date=last_day(period),
                                    source_kind="recurring", source_ref=ref, lines=lines, memo=t.description,
                                    related_document_id=t.related_document_id,
                                    relation="depreciates_asset" if t.offset_category_code == "B01" else None)
            else:
                inp = DocumentInput(doc_type="payable", company_id=t.company_id, doc_date=day_in_period(period, 1),
                                    source_kind="recurring", source_ref=ref, counterparty_id=t.counterparty_id,
                                    settle_amount=amount, has_invoice=t.has_invoice, memo=t.description,
                                    due_date=day_in_period(period, t.due_day) if t.due_day else None,
                                    lines=[LineInput(period, t.category_code, amount, t.store_id, t.description)])
            self.posting.create(inp, actor)
            created += 1
            if t.end_period and add_months(period, 1) == t.end_period:
                self.ops.open_review("recurring_ending", t.company_id, f"recurring:{t.id}",
                                     {"template_id": str(t.id), "description": t.description,
                                      "end_period": t.end_period}, period_key=period)
        for t in self.ops.accrual_templates_for(period, mode="provision"):
            ref = f"{t.id}:{period}"
            if self.docs.get_by_source("accrual", ref) or self.ops.is_closed(t.company_id, period):
                skipped += 1
                continue
            self.create_estimate(t.id, period, t.fixed_amount or 0, actor)
            created += 1
        return {"created": created, "skipped": skipped}


class AdvanceService:
    def __init__(self, session: Session):
        self.posting = PostingService(session)
        self.docs, self.ops, self.catalog = self.posting.docs, self.posting.ops, self.posting.catalog
        self.cfg = get_settings()

    def recognize(self, document_id: uuid.UUID, amount: int, period: str, category_code: str, actor: str,
                  doc_date: date | None = None, store_id: str | None = None) -> Document:
        """把預收款轉成營收。amount 為含稅金額（與收款同口徑）；營收明細依是否開發票換算未稅。"""
        adv = self.docs.get(document_id, lock=True)
        if adv is None or adv.doc_type != "advance_receipt" or adv.status != "posted":
            raise NotFound("找不到預收款")
        remaining = (adv.settle_amount or 0) - adv.recognized_amount
        if amount <= 0 or amount > remaining:
            raise ExceedsAdvance(f"這筆預收只剩 {remaining:,} 元可轉")
        cat = self.catalog.category(category_code)
        if cat is None or cat.pl_section != "revenue":
            raise ValidationFailed("轉營收要選營收類別（R01–R05）")
        revenue = untaxed(amount, adv.has_invoice, self.cfg.tax_rate)
        store = store_id
        n = len(self.docs.related(adv.id, "recognizes_advance"))
        doc = self.posting.create(DocumentInput(
            doc_type="journal", company_id=adv.company_id, doc_date=doc_date or last_day(period),
            source_kind="system", source_ref=f"recognize:{adv.id}:{n + 1}", counterparty_id=adv.counterparty_id,
            related_document_id=adv.id, relation="recognizes_advance", memo=f"預收轉營收（{adv.memo or ''}）",
            lines=[LineInput(period, category_code, revenue, store, "預收轉營收")]), actor)
        adv.recognized_amount += amount
        adv.version += 1
        return doc

    def list(self, company_ids: set[str] | None, status: str | None, delivery_before: date | None) -> list[Document]:
        docs, _ = self.docs.list(company_ids=company_ids, doc_type="advance_receipt", status="posted",
                                 page_size=500)
        out = []
        for d in docs:
            rem = (d.settle_amount or 0) - d.recognized_amount
            st = "recognized" if rem == 0 else ("partial" if d.recognized_amount else "unrecognized")
            if status and st != status:
                continue
            if delivery_before and d.expected_delivery_date and d.expected_delivery_date > delivery_before:
                continue
            out.append(d)
        return out
