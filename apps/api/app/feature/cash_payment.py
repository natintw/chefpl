"""現金付款：應付（或應收）用現金結清時，由會計在帳務系統標記。

銀行付款由現金流系統的 webhook 自動結清；現金沒有銀行交易可對，所以在這裡手動標。
- 一次付款可以付好幾張單據（例：門市收銀一次付清菜商一週的單），共用一個付款 ID，取消時整筆一起取消。
- 預設付清每張單據的未結清金額；只有一張時可以指定金額（少付、分次付）。
- 同一次付款的單據必須是同一個法人（總公司＋分公司），因為現金是同一個法人的錢。
- 不影響損益：損益依發生月份認列。只改「已付／未付」和應付餘額，所以已關帳的月份也可以標。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFound, ValidationFailed
from app.feature.models import Document, Settlement
from app.feature.posting import PostingService
from app.seams.company import get_directory

SOURCES = {"petty_cash": "零用金", "store_cash": "門市收銀", "advance": "老闆／員工代墊"}
SETTLEABLE = {"payable", "receivable"}


class CashPaymentService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs = self.posting.docs
        self.dir = get_directory()

    def pay(self, document_ids: list[str], paid_date: date, source: str, actor: str, *,
            note: str | None = None, amount: int | None = None) -> dict[str, Any]:
        if source not in SOURCES:
            raise ValidationFailed(f"付款來源只能是：{'、'.join(SOURCES.values())}")
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            raise ValidationFailed("沒有選任何單據")
        if amount is not None and len(ids) > 1:
            raise ValidationFailed("指定金額只能用在一張單據；多張單據會各自付清未結清金額")
        docs: list[Document] = []
        for raw in ids:
            doc = self.docs.get(uuid.UUID(str(raw)), lock=True)
            if doc is None:
                raise NotFound(f"單據 {raw} 不存在")
            name = doc.counterparty.name if doc.counterparty else "（未指定對象）"
            if doc.status == "void":
                raise ValidationFailed(f"{name} 的單據已作廢")
            if doc.doc_type not in SETTLEABLE:
                raise ValidationFailed(f"{name} 的單據不需要結清")
            if self._outstanding(doc) <= 0:
                raise ValidationFailed(f"{name} 的單據已經結清")
            docs.append(doc)
        entities = {self.dir.legal_entity(d.company_id) for d in docs}
        if len(entities) > 1:
            raise ValidationFailed("一次現金付款只能付同一家公司（含分公司）的單據；不同公司請分開標記")
        if amount is not None:
            if amount <= 0:
                raise ValidationFailed("金額要大於 0")
            if amount > self._outstanding(docs[0]):
                raise ValidationFailed(f"金額 {amount:,} 超過未結清 {self._outstanding(docs[0]):,}")

        payment_id = f"cash-{uuid.uuid4().hex[:24]}"
        out, total = [], 0
        for doc in docs:
            amt = amount if amount is not None else self._outstanding(doc)
            self.docs.add_settlement(Settlement(
                document_id=doc.id, cash_txn_id=payment_id, cash_account_id=f"cash:{source}", txn_date=paid_date,
                amount=amt, bank_amount=None, match_level="manual", note=note, created_by=actor))
            self.s.refresh(doc)
            self.posting.recompute_settlement(doc)
            out.append({"document_id": str(doc.id), "amount": amt, "settle_status": doc.settle_status})
            total += amt
        self.s.flush()
        return {"payment_id": payment_id, "source": source, "source_label": SOURCES[source],
                "paid_date": paid_date.isoformat(), "total": total, "documents": out}

    def reverse(self, payment_id: str, actor: str) -> dict[str, Any]:
        if not payment_id.startswith("cash-"):
            raise ValidationFailed("這不是現金付款；銀行付款請在現金流系統處理")
        rows = list(self.s.scalars(select(Settlement).where(Settlement.cash_txn_id == payment_id,
                                                            Settlement.reversed_at.is_(None))))
        if not rows:
            raise NotFound("找不到這筆現金付款（可能已取消）")
        now = datetime.now(timezone.utc)
        for st in rows:
            st.reversed_at = now
            st.note = ((st.note + "；") if st.note else "") + f"{actor} 取消"
        self.s.flush()
        reopened = []
        for doc_id in dict.fromkeys(st.document_id for st in rows):
            doc = self.docs.get(doc_id)
            self.s.refresh(doc)
            self.posting.recompute_settlement(doc)
            reopened.append(str(doc_id))
        return {"payment_id": payment_id, "reopened_documents": reopened}

    @staticmethod
    def _outstanding(doc: Document) -> int:
        return (doc.settle_amount or 0) - sum(st.amount for st in doc.settlements if st.reversed_at is None)
