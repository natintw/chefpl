"""與現金流系統的交接（合約：contracts/integration-cashflow.yaml）。

open-items：未結清清單；settlements：回寫核對結果；bank-only-items：銀行有、單據無的收支。
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.feature.common import SETTLEABLE_TYPES
from app.feature.models import Document, Settlement
from app.feature.posting import PostingService
from app.seams.company import get_directory


def _enc(updated_at: datetime, doc_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps({"u": updated_at.isoformat(), "i": str(doc_id)}).encode()).decode()


def _dec(cursor: str) -> tuple[datetime, uuid.UUID]:
    d = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    return datetime.fromisoformat(d["u"]), uuid.UUID(d["i"])


def serialize_open_item(d: Document) -> dict[str, Any]:
    cp = d.counterparty
    window = ({"from": (d.due_date - timedelta(days=5)).isoformat(), "to": (d.due_date + timedelta(days=10)).isoformat()}
              if d.due_date else None)
    return {
        "document_id": str(d.id), "doc_type": d.doc_type, "company_id": d.company_id,
        "store_ids": sorted({l.store_id for l in d.lines if l.store_id}),
        "counterparty": {
            "id": str(cp.id) if cp else None, "name": cp.name if cp else "（未指定）", "tax_id": cp.tax_id if cp else None,
            "keywords": list(cp.keywords) if cp else [], "bank_account_no_masked": None,
            "is_intercompany": bool(cp and cp.is_intercompany),
            "intercompany_company_id": cp.intercompany_company_id if cp else None},
        "doc_date": d.doc_date.isoformat(), "period_keys": sorted({l.period_key for l in d.lines}),
        "due_date": d.due_date.isoformat() if d.due_date else None, "payment_window": window,
        "settle_amount": d.settle_amount or 0, "settled_amount": d.settled_amount,
        "outstanding": (d.settle_amount or 0) - d.settled_amount, "invoice_nos": list(d.invoice_nos),
        "has_invoice": d.has_invoice,
        "has_balance_lines": any(l.category_code.startswith("B") for l in d.lines),
        "source_kind": d.source_kind,
        "expected_delivery_date": d.expected_delivery_date.isoformat() if d.expected_delivery_date else None,
        "memo": d.memo, "version": d.version, "updated_at": d.updated_at.isoformat(),
    }


class SettlementService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.ops = self.posting.docs, self.posting.ops
        self.dir = get_directory()
        self.cfg = get_settings()

    # ------------------------------------------------------------------ open-items
    def open_items(self, *, company_id: str | None, doc_types: list[str] | None, due_before: date | None,
                   updated_after: datetime | None, cursor: str | None, limit: int) -> dict[str, Any]:
        types = doc_types or ["payable", "receivable", "advance_receipt"]
        as_of = datetime.now(timezone.utc)
        rows = self.docs.open_items(company_id=company_id, doc_types=types, due_before=due_before,
                                    updated_after=updated_after, after_key=_dec(cursor) if cursor else None,
                                    limit=limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        removed = ([str(i) for i in self.docs.removed_since(company_id=company_id, doc_types=types,
                                                            updated_after=updated_after)]
                   if updated_after and not cursor else [])
        return {"as_of": as_of.isoformat(), "items": [serialize_open_item(d) for d in rows], "removed": removed,
                "next_cursor": _enc(rows[-1].updated_at, rows[-1].id) if more else None}

    # ------------------------------------------------------------------ settlements
    def apply(self, items: list[dict[str, Any]], actor: str) -> dict[str, Any]:
        applied = duplicates = queued = 0
        rejected: list[dict] = []
        touched: dict[uuid.UUID, Document] = {}

        def reject(i: int, it: dict, code: str, msg: str) -> None:
            rejected.append({"index": i, "document_id": it.get("document_id"), "cash_txn_id": it.get("cash_txn_id"),
                             "code": code, "message": msg})

        for i, it in enumerate(items):
            try:
                doc_id = uuid.UUID(str(it["document_id"]))
            except (KeyError, ValueError):
                reject(i, it, "INVALID_FIELD", "document_id 格式錯誤")
                continue
            doc = self.docs.get(doc_id, lock=True)
            if doc is None:
                reject(i, it, "DOCUMENT_NOT_FOUND", "單據不存在")
                continue
            if doc.status == "void":
                reject(i, it, "DOCUMENT_VOID", "單據已作廢")
                continue
            if doc.doc_type not in SETTLEABLE_TYPES:
                reject(i, it, "DOCUMENT_NOT_SETTLEABLE", "這種單據不需要結清")
                continue
            acct = self.dir.get_bank_account(it["cash_account_id"])
            if acct is None or not self.dir.same_legal_entity(acct.company_id, doc.company_id):
                reject(i, it, "COMPANY_MISMATCH", "銀行帳戶與單據不屬於同一家公司")
                continue

            if it.get("action", "apply") == "reverse":
                st = self.docs.active_settlement(doc.id, it["cash_txn_id"])
                if st is None:
                    reject(i, it, "SETTLEMENT_NOT_FOUND", "找不到要取消的配對")
                    continue
                st.reversed_at = datetime.now(timezone.utc)
                self.s.flush()
                self.s.refresh(doc)
                self.posting.recompute_settlement(doc)
                touched[doc.id] = doc
                applied += 1
                continue

            if self.docs.active_settlement(doc.id, it["cash_txn_id"]):
                duplicates += 1
                touched[doc.id] = doc
                continue
            if it["match_level"] == "amount_only":
                self.ops.open_review("amount_only_match", doc.company_id, f"cash_txn:{it['cash_txn_id']}",
                                     {"item": it, "document_id": str(doc.id)})
                queued += 1
                continue
            bank_amount = it.get("bank_amount")
            if bank_amount and self.docs.allocated_for_txn(it["cash_txn_id"]) + it["amount"] > bank_amount:
                reject(i, it, "TXN_OVER_ALLOCATED", "這筆銀行交易分配出去的金額超過交易金額")
                continue
            self.docs.add_settlement(Settlement(
                document_id=doc.id, cash_txn_id=it["cash_txn_id"], cash_account_id=it["cash_account_id"],
                txn_date=date.fromisoformat(str(it["txn_date"])), amount=int(it["amount"]),
                bank_amount=bank_amount, match_level=it["match_level"]))
            self.s.refresh(doc)
            self.posting.recompute_settlement(doc)
            self.ops.close_reviews_for_ref(f"cash_txn:{it['cash_txn_id']}", {"settled": str(doc.id)}, actor)
            touched[doc.id] = doc
            applied += 1

        self.s.flush()
        return {"applied": applied, "duplicates": duplicates, "queued_for_review": queued, "rejected": rejected,
                "documents": [{"document_id": str(d.id), "settle_status": d.settle_status,
                               "settled_amount": d.settled_amount,
                               "outstanding": (d.settle_amount or 0) - d.settled_amount} for d in touched.values()]}

    # ------------------------------------------------------------------ bank-only-items
    def bank_only(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        received = duplicates = 0
        rejected = []
        for i, it in enumerate(items):
            ref = f"cash_txn:{it['cash_txn_id']}"
            if self.ops.review_exists("bank_only", ref) or self.docs.get_by_source("bank_only", it["cash_txn_id"]) \
                    or self.docs.txn_already_used(it["cash_txn_id"]):
                duplicates += 1
                continue
            if self.dir.get_company(it["company_id"]) is None:
                rejected.append({"index": i, "document_id": None, "cash_txn_id": it["cash_txn_id"],
                                 "code": "INVALID_FIELD", "message": "公司不存在"})
                continue
            period = str(it["txn_date"])[:7]
            self.ops.open_review("bank_only", it["company_id"], ref, {"item": it}, period_key=period)
            received += 1
        return {"received": received, "duplicates": duplicates, "rejected": rejected}
