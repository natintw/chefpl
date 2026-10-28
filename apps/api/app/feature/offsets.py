"""預付款、折讓、代付與沖抵（開發文件 v0.6：3.8、5.13–5.15）。

沖抵＝不經過銀行的減少，記在 document_offsets：
- prepayment     預付單 → 應付：訂金抵發票；另產生一張調整單 B07 −x（預付餘額歸零）
- allowance      折讓單 → 原應付：尾款少付
- vendor_credit  折讓單（廠商欠我們）→ 下一張應付：下次進貨抵
- paid_by_other  付款公司的代付單 → 受益公司的應付：別家公司已經把錢付了

只有「被沖的一方」（to）累計 offset_amount；尚欠＝結清金額 − 銀行已付 − offset_amount（common.outstanding）。
沖抵、對單、代付都只用統編、發票號碼、金額等確定性規則，不使用 AI（D21）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, literal, any_, select
from sqlalchemy.orm import Session

from app.errors import (ExceedsOriginal, ExceedsOutstanding, ExceedsPrepayment, HasSettlements, NotFound,
                        SameCompany, ValidationFailed)
from app.feature.common import DocumentInput, LineInput, outstanding, period_of
from app.feature.models import Account, Counterparty, Document, DocumentOffset, ReviewItem
from app.feature.posting import PostingService
from app.feature.repositories import CatalogRepository, OpsRepository
from app.seams.company import get_directory

OFFSET_KINDS = {"prepayment", "allowance", "vendor_credit", "paid_by_other"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OffsetService:
    def __init__(self, session: Session):
        self.s = session
        self.dir = get_directory()
        self.ops = OpsRepository(session)

    # ------------------------------------------------------------------ 查詢
    def used(self, from_doc_id: uuid.UUID) -> int:
        return int(self.s.scalar(select(func.coalesce(func.sum(DocumentOffset.amount), 0)).where(
            DocumentOffset.from_document_id == from_doc_id, DocumentOffset.reversed_at.is_(None))) or 0)

    def remaining(self, doc: Document) -> int:
        """預付單、折讓單還能拿去沖的金額。"""
        return (doc.settle_amount or 0) - self.used(doc.id)

    def _entity_companies(self, company_id: str) -> list[str]:
        ent = self.dir.legal_entity(company_id)
        return [c.id for c in self.dir.list_companies() if self.dir.legal_entity(c.id) == ent]

    def vendors_with_open_prepayments(self) -> set[uuid.UUID]:
        used = (select(DocumentOffset.from_document_id, func.sum(DocumentOffset.amount).label("u"))
                .where(DocumentOffset.reversed_at.is_(None)).group_by(DocumentOffset.from_document_id).subquery())
        rows = self.s.execute(select(Document.counterparty_id).outerjoin(used, used.c.from_document_id == Document.id)
                              .where(Document.doc_type == "prepayment", Document.status == "posted",
                                     Document.settle_amount > func.coalesce(used.c.u, 0)))
        return {r[0] for r in rows if r[0]}

    def open_prepayments(self, company_id: str, counterparty_id: uuid.UUID,
                         before: date | None = None) -> list[dict[str, Any]]:
        """同一法人、同一家廠商、還有剩的預付，先付的排前面（5.13）。"""
        stmt = (select(Document).where(Document.doc_type == "prepayment", Document.status == "posted",
                                       Document.counterparty_id == counterparty_id,
                                       Document.company_id.in_(self._entity_companies(company_id)))
                .order_by(Document.doc_date, Document.created_at))
        if before:
            stmt = stmt.where(Document.doc_date <= before)
        out = []
        for d in self.s.scalars(stmt):
            rem = self.remaining(d)
            if rem > 0:
                out.append({"document_id": str(d.id), "doc_date": d.doc_date.isoformat(), "remaining": rem,
                            "amount": d.settle_amount, "memo": d.memo, "company_id": d.company_id,
                            "expected_invoice_period": d.expected_invoice_period,
                            "planned_category_code": d.planned_category_code,
                            "paid": d.settle_status in ("settled", "over")})
        return out

    def list_for(self, doc_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = self.s.scalars(select(DocumentOffset).where(
            (DocumentOffset.from_document_id == doc_id) | (DocumentOffset.to_document_id == doc_id))
            .order_by(DocumentOffset.created_at))
        return [{"id": str(o.id), "kind": o.kind, "from_document_id": str(o.from_document_id),
                 "to_document_id": str(o.to_document_id), "amount": o.amount, "auto_applied": o.auto_applied,
                 "journal_document_id": str(o.journal_document_id) if o.journal_document_id else None,
                 "created_at": o.created_at.isoformat() if o.created_at else None, "created_by": o.created_by,
                 "reversed_at": o.reversed_at.isoformat() if o.reversed_at else None} for o in rows]

    # ------------------------------------------------------------------ 沖抵
    def apply(self, kind: str, from_id: uuid.UUID, to_id: uuid.UUID, amount: int, actor: str,
              auto: bool = False) -> DocumentOffset:
        if kind not in OFFSET_KINDS:
            raise ValidationFailed("沖抵種類不正確")
        posting = PostingService(self.s)
        src = posting.docs.get(from_id, lock=True)
        dst = posting.docs.get(to_id, lock=True)
        if src is None or dst is None or src.status != "posted" or dst.status != "posted":
            raise NotFound("單據不存在、未入帳或已作廢")
        if amount <= 0:
            raise ValidationFailed("沖抵金額要大於 0")
        if dst.doc_type != "payable":
            raise ValidationFailed("只能沖到應付單據")
        if kind in ("prepayment", "allowance", "vendor_credit"):
            want = "prepayment" if kind == "prepayment" else "allowance"
            if src.doc_type != want:
                raise ValidationFailed("沖抵來源的單據類型不對")
            if src.counterparty_id != dst.counterparty_id:
                raise ValidationFailed("預付或折讓的廠商與這張應付不同")
            if not self.dir.same_legal_entity(src.company_id, dst.company_id):
                raise ValidationFailed("預付或折讓與應付不是同一家公司")
            rem = self.remaining(src)
            if amount > rem:
                raise ExceedsPrepayment(f"這筆只剩 {rem:,} 元可沖")
        left = outstanding(dst)
        if amount > left:
            raise ExceedsOutstanding(f"這張應付只剩 {left:,} 元未付")

        journal_id = None
        if kind == "prepayment":
            j = posting.create(DocumentInput(
                doc_type="journal", company_id=src.company_id, doc_date=dst.doc_date, source_kind="system",
                source_ref=None, related_document_id=src.id, relation="offsets_prepayment",
                counterparty_id=src.counterparty_id, memo=f"預付沖抵：{', '.join(dst.invoice_nos) or '應付'}",
                lines=[LineInput(period_key=period_of(dst.doc_date), category_code="B07", amount=-amount,
                                 counterparty_id=src.counterparty_id, description="預付款沖抵發票")]), actor)
            journal_id = j.id
        off = DocumentOffset(kind=kind, from_document_id=src.id, to_document_id=dst.id, amount=amount,
                             journal_document_id=journal_id, auto_applied=auto, created_by=actor)
        self.s.add(off)
        dst.offset_amount = (dst.offset_amount or 0) + amount
        if kind == "paid_by_other":
            dst.paid_by_company_id = dst.paid_by_company_id or src.company_id
        self.s.flush()
        posting.recompute_settlement(dst)
        if src.doc_type == "allowance":
            posting.recompute_settlement(src)
        self._close_reviews(dst.id, actor, off)
        self.s.flush()
        return off

    def reverse(self, offset_id: uuid.UUID, reason: str, actor: str) -> DocumentOffset:
        if not reason:
            raise ValidationFailed("取消沖抵需填原因")
        off = self.s.get(DocumentOffset, offset_id)
        if off is None or off.reversed_at is not None:
            raise NotFound("沖抵紀錄不存在或已取消")
        posting = PostingService(self.s)
        dst = posting.docs.get(off.to_document_id, lock=True)
        if any(st.reversed_at is None for st in dst.settlements) and outstanding(dst) <= 0:
            raise HasSettlements("這張應付已依沖抵後的金額付款結清，請先在現金流系統取消配對")
        if off.journal_document_id:
            posting.void(off.journal_document_id, f"取消沖抵：{reason}", actor, force=True)
        off.reversed_at, off.reversed_by, off.reverse_reason = _now(), actor, reason
        dst.offset_amount = max(0, (dst.offset_amount or 0) - off.amount)
        self.s.flush()
        posting.recompute_settlement(dst)
        src = posting.docs.get(off.from_document_id)
        if src is not None and src.doc_type == "allowance":
            posting.recompute_settlement(src)
        self.s.flush()
        return off

    def _close_reviews(self, doc_id: uuid.UUID, actor: str, off: DocumentOffset) -> None:
        if off.kind != "prepayment":
            return
        for it in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "prepayment_match",
                                                          ReviewItem.ref == f"document:{doc_id}",
                                                          ReviewItem.status == "open")):
            it.status, it.resolved_by, it.resolved_at = "resolved", actor, _now()
            it.resolution = {"action": "apply_offset", "offset_id": str(off.id), "amount": off.amount}

    # ------------------------------------------------------------------ 預付款
    def create_prepayment(self, *, company_id: str, counterparty_id: uuid.UUID, doc_date: date, amount: int,
                          actor: str, store_id: str | None = None, settlement_method: str = "pending",
                          expected_invoice_period: str | None = None, planned_category_code: str | None = None,
                          order_total: int | None = None, memo: str | None = None,
                          due_date: date | None = None) -> Document:
        if amount <= 0:
            raise ValidationFailed("預付金額要大於 0", [{"field": "amount", "message": "要大於 0"}])
        if order_total is not None and order_total < amount:
            raise ValidationFailed("整筆訂購總額不能小於預付金額", [{"field": "order_total", "message": "太小"}])
        cat = CatalogRepository(self.s)
        if planned_category_code and cat.category(planned_category_code) is None:
            raise ValidationFailed("預定類別不存在")
        return PostingService(self.s).create(DocumentInput(
            doc_type="prepayment", company_id=company_id, doc_date=doc_date, counterparty_id=counterparty_id,
            settle_amount=amount, settlement_method=settlement_method, due_date=due_date or doc_date, memo=memo,
            expected_invoice_period=expected_invoice_period, planned_category_code=planned_category_code,
            order_total=order_total,
            lines=[LineInput(period_key=period_of(doc_date), category_code="B07", amount=amount, store_id=store_id,
                             counterparty_id=counterparty_id, description=memo or "預付款")]), actor)

    # ------------------------------------------------------------------ 折讓
    def lookup_invoice(self, invoice_no: str, seller_tax_id: str | None = None,
                       company_ids: set[str] | None = None) -> list[Document]:
        stmt = select(Document).where(Document.status == "posted", Document.doc_type == "payable",
                                      literal(invoice_no.strip().upper()) == any_(Document.invoice_nos))
        if seller_tax_id:
            stmt = stmt.join(Counterparty, Counterparty.id == Document.counterparty_id).where(
                Counterparty.tax_id == seller_tax_id)
        if company_ids is not None:
            stmt = stmt.where(Document.company_id.in_(company_ids))
        return list(self.s.scalars(stmt.limit(5)).unique())

    def original_candidates(self, counterparty_id: uuid.UUID, company_id: str | None, min_amount: int = 0,
                            since: date | None = None, limit: int = 20) -> list[Document]:
        stmt = select(Document).where(Document.status == "posted", Document.doc_type == "payable",
                                      Document.counterparty_id == counterparty_id,
                                      func.coalesce(Document.settle_amount, 0) >= min_amount)
        if company_id:
            stmt = stmt.where(Document.company_id.in_(self._entity_companies(company_id)))
        if since:
            stmt = stmt.where(Document.doc_date >= since)
        return list(self.s.scalars(stmt.order_by(Document.doc_date.desc()).limit(limit)).unique())

    def allowance_room(self, original: Document) -> int:
        """原單還能折讓多少（含稅）＝原單金額 − 之前的折讓。"""
        prev = self.s.scalar(select(func.coalesce(func.sum(Document.settle_amount), 0)).where(
            Document.doc_type == "allowance", Document.related_document_id == original.id,
            Document.status != "void")) or 0
        return (original.settle_amount or 0) - int(prev)

    def create_allowance(self, *, original_document_id: uuid.UUID | None, company_id: str | None,
                         counterparty_id: uuid.UUID | None, doc_date: date, reason: str, untaxed: int, tax: int,
                         refund_method: str, actor: str, allowance_no: str | None = None,
                         period: str | None = None, category_code: str | None = None, store_id: str | None = None,
                         memo: str | None = None, lines: list[LineInput] | None = None) -> Document:
        """period：記帳月份，不給＝原單月份（未關帳時）或折讓當月；lines 可自訂（負數），不給就照原單第一行帶入。"""
        if untaxed <= 0 or tax < 0:
            raise ValidationFailed("折讓的未稅金額要大於 0，稅額不能是負數（系統會轉成負數記帳）")
        total = untaxed + tax
        posting = PostingService(self.s)
        orig = None
        if original_document_id:
            orig = posting.docs.get(original_document_id)
            if orig is None or orig.status != "posted" or orig.doc_type != "payable":
                raise NotFound("原單不存在、未入帳或不是應付")
            company_id = orig.company_id
            counterparty_id = orig.counterparty_id
            room = self.allowance_room(orig)
            if total > room:
                raise ExceedsOriginal(f"原單扣掉之前的折讓，只剩 {room:,} 元")
            if refund_method == "reduce_outstanding" and total > outstanding(orig):
                raise ExceedsOutstanding(f"原單只剩 {outstanding(orig):,} 元未付；已付清的部分請選「廠商退錢」或「下次進貨抵」")
        elif refund_method == "reduce_outstanding":
            raise ValidationFailed("沒有原單時，請選「廠商退錢」或「下次進貨抵」")
        if not company_id or not counterparty_id:
            raise ValidationFailed("沒有原單時要指定公司與廠商")
        if lines is None:
            if orig is not None:
                src_line = next((l for l in orig.lines if not l.category_code.startswith("B")), orig.lines[0])
                orig_period = src_line.period_key
                use_period = period or (orig_period if not self.ops.is_closed(src_line.company_id, orig_period)
                                        else period_of(doc_date))
                lines = [LineInput(period_key=use_period, category_code=category_code or src_line.category_code,
                                   amount=-untaxed, store_id=store_id if store_id is not None else src_line.store_id,
                                   original_document_id=orig.id, description=memo or "折讓")]
            else:
                if not category_code:
                    raise ValidationFailed("沒有原單時要選類別")
                lines = [LineInput(period_key=period or period_of(doc_date), category_code=category_code,
                                   amount=-untaxed, store_id=store_id, description=memo or "折讓")]
        doc = posting.create(DocumentInput(
            doc_type="allowance", company_id=company_id, doc_date=doc_date, counterparty_id=counterparty_id,
            settle_amount=total, tax_amount=-tax, has_invoice=bool(orig and orig.has_invoice), memo=memo,
            original_invoice_no=(orig.invoice_nos[0] if orig and orig.invoice_nos else None),
            allowance_no=allowance_no, allowance_reason=reason, refund_method=refund_method,
            related_document_id=orig.id if orig else None, relation="allowance_of" if orig else None,
            invoice_nos=[allowance_no] if allowance_no else [], lines=lines, check_prepayments=False), actor)
        if refund_method == "reduce_outstanding" and orig is not None:
            self.apply("allowance", doc.id, orig.id, total, actor)
        return doc

    # ------------------------------------------------------------------ 代付（集團內）
    def company_counterparty(self, company_id: str) -> Counterparty:
        """集團內公司在對象主檔裡的那一筆（B05 往來的對象）。沒有就依公司主檔建立。"""
        cp = self.s.scalar(select(Counterparty).where(Counterparty.intercompany_company_id == company_id))
        if cp:
            return cp
        c = self.dir.get_company(company_id)
        if c is None:
            raise ValidationFailed("公司不存在")
        cat = CatalogRepository(self.s)
        cp = cat.counterparty_by_tax_id(c.tax_id) if c.tax_id else cat.counterparty_by_name(c.name)
        if cp:
            if not cp.intercompany_company_id:
                cp.is_intercompany, cp.intercompany_company_id = True, company_id
            return cp
        return cat.add_counterparty(name=c.name, tax_id=c.tax_id or None, roles=["vendor", "customer"],
                                    is_intercompany=True, intercompany_company_id=company_id, aliases=[],
                                    keywords=[], bank_accounts=[])

    def create_on_behalf(self, *, beneficiary_company_id: str, store_id: str | None, vendor_id: uuid.UUID,
                         category_code: str, payer_company_id: str, doc_date: date, untaxed: int, tax: int,
                         actor: str, invoice_no: str | None = None, invoice_buyer_tax_id: str | None = None,
                         has_invoice: bool = True, settlement_method: str = "pending", paid_on: date | None = None,
                         memo: str | None = None) -> dict[str, Any]:
        """集團內代付（5.15）：一次建立受益方應付＋往來、付款方代付單，並把受益方應付沖成「他公司已付」。"""
        if beneficiary_company_id == payer_company_id:
            raise SameCompany("付款公司和費用公司相同，請改用一般支出")
        if self.dir.same_legal_entity(beneficiary_company_id, payer_company_id):
            raise SameCompany("總公司與分公司是同一法人，可以直接用總公司帳戶付分公司的帳，請改用一般支出")
        if untaxed <= 0 or tax < 0:
            raise ValidationFailed("金額不正確")
        total = untaxed + tax
        posting = PostingService(self.s)
        period = period_of(doc_date)
        payer = self.dir.get_company(payer_company_id)
        bene = self.dir.get_company(beneficiary_company_id)
        if payer is None or bene is None:
            raise ValidationFailed("公司不存在")
        payer_cp = self.company_counterparty(payer_company_id)
        bene_cp = self.company_counterparty(beneficiary_company_id)
        label = memo or "代付"

        d1 = posting.create(DocumentInput(
            doc_type="payable", company_id=beneficiary_company_id, doc_date=doc_date, counterparty_id=vendor_id,
            settle_amount=total, tax_amount=tax, has_invoice=has_invoice,
            invoice_nos=[invoice_no] if invoice_no else [], memo=f"{label}（{payer.name}代付）",
            paid_by_company_id=payer_company_id, invoice_buyer_tax_id=invoice_buyer_tax_id,
            check_prepayments=False,
            lines=[LineInput(period_key=period, category_code=category_code, amount=untaxed, store_id=store_id,
                             description=label)]), actor)

        header_is_payer = bool(invoice_buyer_tax_id and payer.tax_id and invoice_buyer_tax_id == payer.tax_id)
        d2_lines = [LineInput(period_key=period, category_code="B05", amount=total, counterparty_id=bene_cp.id,
                              description=f"代付 {bene.name}：{label}")]
        if header_is_payer:  # D24：發票抬頭是付款公司 → 代付（進項）＋代支出（銷項），一進一出
            acc = self._override_accounts()
            d2_lines += [
                LineInput(period_key=period, category_code="B05", amount=total, counterparty_id=bene_cp.id,
                          account_override_id=acc.get("OB-IN"),
                          description=f"代付（進項）：未稅 {untaxed:,}＋進項稅 {tax:,}"),
                LineInput(period_key=period, category_code="B05", amount=-total, counterparty_id=bene_cp.id,
                          account_override_id=acc.get("OB-OUT"),
                          description=f"代支出（銷項）：轉給 {bene.name}")]
        # 付款方的代付單由系統產生（同一張發票，不重複檢查）；發票號碼保留，之後發票系統送來同一張時才認得出來
        d2 = posting.create(DocumentInput(
            doc_type="payable", company_id=payer_company_id, doc_date=doc_date, counterparty_id=vendor_id,
            source_kind="system",
            settle_amount=total, tax_amount=0, has_invoice=has_invoice, settlement_method=settlement_method,
            invoice_nos=[invoice_no] if invoice_no else [], memo=f"代付 {bene.name}：{label}",
            due_date=paid_on, on_behalf_of_company_id=beneficiary_company_id,
            invoice_buyer_tax_id=invoice_buyer_tax_id, related_document_id=d1.id, relation="paid_on_behalf",
            check_prepayments=False, lines=d2_lines), actor)

        j1 = posting.create(DocumentInput(
            doc_type="journal", company_id=beneficiary_company_id, doc_date=doc_date, source_kind="system",
            counterparty_id=payer_cp.id, related_document_id=d1.id, relation="paid_on_behalf",
            memo=f"{payer.name}代付：{label}",
            lines=[LineInput(period_key=period, category_code="B05", amount=-total, store_id=None,
                             counterparty_id=payer_cp.id, description=f"欠 {payer.name}（代付）")]), actor)

        self.apply("paid_by_other", d2.id, d1.id, total, actor)
        warnings = []
        if invoice_buyer_tax_id and not header_is_payer and invoice_buyer_tax_id != (bene.tax_id or ""):
            warnings.append("INVOICE_BUYER_MISMATCH")
            self.ops.open_review("invoice_buyer_mismatch", beneficiary_company_id, f"document:{d1.id}",
                                 {"document_id": str(d1.id), "buyer_tax_id": invoice_buyer_tax_id,
                                  "beneficiary": bene.name, "payer": payer.name}, period_key=period)
        return {"beneficiary_document_id": str(d1.id), "payer_document_id": str(d2.id),
                "journal_document_id": str(j1.id), "header_is_payer": header_is_payer, "warnings": warnings}

    def _override_accounts(self) -> dict[str, uuid.UUID]:
        """代付（進項）、代支出（銷項）兩個科目（migration 0013 建立；萬一被刪就補回）。代號待德昇科目表確定。"""
        want = {"OB-IN": "代付（進項）", "OB-OUT": "代支出（銷項）"}
        acc = {a.code: a.id for a in self.s.scalars(select(Account).where(Account.code.in_(tuple(want))))}
        for code, name in want.items():
            if code not in acc:
                a = Account(code=code, name=name, kind="balance_sheet", is_active=True)
                self.s.add(a)
                self.s.flush()
                acc[code] = a.id
        return acc

    def void_on_behalf(self, any_doc_id: uuid.UUID, reason: str, actor: str) -> None:
        """三筆一起作廢；付款方代付單已核對到銀行付款時拒絕。"""
        posting = PostingService(self.s)
        doc = posting.docs.get(any_doc_id)
        if doc is None:
            raise NotFound("單據不存在")
        d1 = doc if doc.relation != "paid_on_behalf" else posting.docs.get(doc.related_document_id)
        group = [d1, *posting.docs.related(d1.id, "paid_on_behalf")]
        payer_doc = next((d for d in group if d.on_behalf_of_company_id), None)
        if payer_doc and any(st.reversed_at is None for st in payer_doc.settlements):
            raise HasSettlements("付款公司的代付單已核對到銀行付款，請先在現金流系統取消配對")
        for o in self.s.scalars(select(DocumentOffset).where(DocumentOffset.to_document_id == d1.id,
                                                             DocumentOffset.reversed_at.is_(None))):
            self.reverse(o.id, reason, actor)
        for d in group:
            posting.void(d.id, reason, actor, force=True)
