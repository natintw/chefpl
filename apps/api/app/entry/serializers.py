"""把 ORM 物件轉成 API 回應（對應 OpenAPI 的 DocumentDetail 等）。"""
from __future__ import annotations

from typing import Any

from app.feature.common import outstanding
from app.feature.models import Document


def _bank_txn(ref: str) -> str:
    return ref[4:] if ref.startswith("adj:") else ref


def document(d: Document, detail: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(d.id), "doc_type": d.doc_type, "source_kind": d.source_kind, "source_ref": d.source_ref,
        "company_id": d.company_id,
        "counterparty": {"id": str(d.counterparty.id), "name": d.counterparty.name,
                         "is_intercompany": d.counterparty.is_intercompany} if d.counterparty else None,
        "doc_date": d.doc_date.isoformat(), "invoice_nos": list(d.invoice_nos), "has_invoice": d.has_invoice,
        "settle_amount": d.settle_amount, "settled_amount": d.settled_amount, "tax_amount": d.tax_amount,
        "offset_amount": d.offset_amount or 0,
        "outstanding": outstanding(d) if d.settle_amount is not None else None,
        "settle_status": d.settle_status, "settled_by": d.settled_by,
        "related_document_id": str(d.related_document_id) if d.related_document_id else None,
        "relation": d.relation, "refund_method": d.refund_method, "allowance_no": d.allowance_no,
        "allowance_reason": d.allowance_reason, "original_invoice_no": d.original_invoice_no,
        "expected_invoice_period": d.expected_invoice_period, "planned_category_code": d.planned_category_code,
        "order_total": d.order_total, "on_behalf_of_company_id": d.on_behalf_of_company_id,
        "paid_by_company_id": d.paid_by_company_id, "invoice_buyer_tax_id": d.invoice_buyer_tax_id,
        "due_date": d.due_date.isoformat() if d.due_date else None, "status": d.status, "memo": d.memo,
        "store_ids": sorted({l.store_id for l in d.lines if l.store_id}),
        "period_keys": sorted({l.period_key for l in d.lines}), "version": d.version,
        # 收付款日：銀行入款／付款日、現金收付日（未沖回的）；bank_txn_ids 用來「取消核對／改對應」
        "settled_dates": sorted({st.txn_date.isoformat() for st in d.settlements if st.reversed_at is None}),
        "bank_txn_ids": sorted({_bank_txn(st.cash_txn_id) for st in d.settlements
                                if st.reversed_at is None and not st.cash_txn_id.startswith("cash-")}
                               | ({_bank_txn(d.source_ref)} if d.source_kind == "bank_only" and d.source_ref
                                  and d.status != "void" else set())),
    }
    if detail:
        out.update({
            "lines": [{"line_no": l.line_no, "period_key": l.period_key, "store_id": l.store_id,
                       "category_code": l.category_code, "amount": l.amount, "description": l.description,
                       "is_intercompany": l.is_intercompany,
                       "counterparty_id": str(l.counterparty_id) if l.counterparty_id else None,
                       "original_document_id": str(l.original_document_id) if l.original_document_id else None,
                       "account_override_id": str(l.account_override_id) if l.account_override_id else None}
                      for l in d.lines],
            "settlements": [{"cash_txn_id": s.cash_txn_id, "cash_account_id": s.cash_account_id,
                             "txn_date": s.txn_date.isoformat(), "amount": s.amount, "bank_amount": s.bank_amount,
                             "match_level": s.match_level,
                             "reversed": s.reversed_at is not None, "note": s.note, "created_by": s.created_by,
                             "kind": "cash" if s.cash_txn_id.startswith("cash-") else "bank"} for s in d.settlements],
            "accrual_mode": d.accrual_mode,
            "expected_delivery_date": d.expected_delivery_date.isoformat() if d.expected_delivery_date else None,
            "recognized_amount": d.recognized_amount,
            "related_document_id": str(d.related_document_id) if d.related_document_id else None,
            "relation": d.relation, "void_reason": d.void_reason, "created_by": d.created_by,
            "import_batch_id": str(d.import_batch_id) if d.import_batch_id else None,
        })
    return out
