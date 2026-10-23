"""把 ORM 物件轉成 API 回應（對應 OpenAPI 的 DocumentDetail 等）。"""
from __future__ import annotations

from typing import Any

from app.feature.models import Document


def document(d: Document, detail: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(d.id), "doc_type": d.doc_type, "source_kind": d.source_kind, "source_ref": d.source_ref,
        "company_id": d.company_id,
        "counterparty": {"id": str(d.counterparty.id), "name": d.counterparty.name,
                         "is_intercompany": d.counterparty.is_intercompany} if d.counterparty else None,
        "doc_date": d.doc_date.isoformat(), "invoice_nos": list(d.invoice_nos), "has_invoice": d.has_invoice,
        "settle_amount": d.settle_amount, "settled_amount": d.settled_amount, "tax_amount": d.tax_amount,
        "outstanding": (d.settle_amount or 0) - d.settled_amount if d.settle_amount is not None else None,
        "settle_status": d.settle_status, "settled_by": d.settled_by,
        "due_date": d.due_date.isoformat() if d.due_date else None, "status": d.status, "memo": d.memo,
        "store_ids": sorted({l.store_id for l in d.lines if l.store_id}),
        "period_keys": sorted({l.period_key for l in d.lines}), "version": d.version,
    }
    if detail:
        out.update({
            "lines": [{"line_no": l.line_no, "period_key": l.period_key, "store_id": l.store_id,
                       "category_code": l.category_code, "amount": l.amount, "description": l.description,
                       "is_intercompany": l.is_intercompany} for l in d.lines],
            "settlements": [{"cash_txn_id": s.cash_txn_id, "cash_account_id": s.cash_account_id,
                             "txn_date": s.txn_date.isoformat(), "amount": s.amount, "match_level": s.match_level,
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
