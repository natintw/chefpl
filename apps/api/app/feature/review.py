"""待確認處理（開發文件 S8）。每一種 kind 有自己的主要動作；任何一種都可以 dismiss。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.errors import NotFound, ValidationFailed
from app.feature.common import DocumentInput, LineInput
from app.feature.models import Settlement
from app.feature.posting import PostingService
from app.feature.settlement import SettlementService


class ReviewService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.ops, self.docs, self.catalog = self.posting.ops, self.posting.docs, self.posting.catalog

    def resolve(self, item_id: uuid.UUID, action: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        item = self.ops.review(item_id)
        if item is None or item.status != "open":
            raise NotFound("待確認項目不存在或已處理")
        result: dict[str, Any] = {"action": action}

        if action == "dismiss":
            item.status = "dismissed"
        elif item.kind == "bank_only" and action == "create_document":
            result["document_id"] = str(self._bank_only_document(item.payload["item"], payload, actor).id)
        elif item.kind == "bank_only" and action == "settle_suggestion":
            # webhook 對不到時附上的建議單據：會計確認後，整組標成已付／已收
            it = item.payload["item"]
            sug = item.payload.get("suggestion") or {}
            picked = set(payload.get("document_ids") or [d["document_id"] for d in sug.get("documents", [])])
            docs = [d for d in sug.get("documents", []) if d["document_id"] in picked]
            if not docs:
                raise ValidationFailed("沒有選任何單據")
            if sum(d["amount"] for d in docs) != int(it["amount"]):
                raise ValidationFailed(f"選的單據合計 {sum(d['amount'] for d in docs):,}，與銀行金額 {int(it['amount']):,} 不同")
            res = SettlementService(self.s).apply([
                {"document_id": d["document_id"], "cash_txn_id": it["cash_txn_id"], "cash_account_id": it["cash_account_id"],
                 "txn_date": it["txn_date"], "amount": d["amount"], "bank_amount": int(it["amount"]),
                 "match_level": "manual", "matched_by": "manual"} for d in docs], actor)
            if res["rejected"]:
                raise ValidationFailed("；".join(r["message"] for r in res["rejected"]))
            result["settled_documents"] = [d["document_id"] for d in docs]
        elif item.kind == "unclassified" and action in ("set_category", "accept_suggestion"):
            code = payload.get("category_code") if action == "set_category" else \
                (item.payload.get("suggestion") or {}).get("category_code")
            if not code:
                raise ValidationFailed("沒有可採用的類別")
            doc = self.docs.get(uuid.UUID(item.payload["document_id"]))
            if doc is None:
                raise NotFound("單據不存在")
            for ln in doc.lines:
                ln.category_code = code
            self.posting.post(doc)
            if payload.get("remember_rule", True) and doc.counterparty_id:
                self.catalog.add_rule("manual", code, actor, counterparty_id=doc.counterparty_id)
            result["document_id"] = str(doc.id)
        elif item.kind == "amount_only_match" and action == "confirm_match":
            it = dict(item.payload["item"], match_level="manual")
            result["settlement"] = SettlementService(self.s).apply([it], actor)
        elif item.kind == "duplicate_source" and action in ("link", "keep_both"):
            if action == "keep_both":
                inp = item.payload.get("pending_input")
                if not inp:
                    raise ValidationFailed("沒有可建立的單據內容")
                result["document_id"] = str(self.posting.create(_input_from_json(inp), actor).id)
        elif item.kind == "new_counterparty" and action == "set_default_category":
            cp = self.catalog.counterparty(uuid.UUID(item.ref.split(":", 1)[1]))
            if cp is None:
                raise NotFound("對象不存在")
            cp.default_category_code = payload["category_code"]
        elif action == "acknowledge":
            pass
        else:
            raise ValidationFailed(f"{item.kind} 不支援 {action}")

        if item.status == "open":
            item.status = "resolved"
        item.resolution, item.resolved_by = {**payload, **result}, actor
        item.resolved_at = datetime.now(timezone.utc)
        self.s.flush()
        return result

    def _bank_only_document(self, it: dict, payload: dict, actor: str):
        lines_in = payload.get("lines") or [{"category_code": payload["category_code"], "amount": it["amount"],
                                             "store_id": payload.get("store_id")}]
        period = str(it["txn_date"])[:7]
        doc_type = "payable" if it["direction"] == "out" else "receivable"
        lines = []
        for ln in lines_in:
            cat = self.catalog.category(ln["category_code"])
            if cat is None:
                raise ValidationFailed(f"類別 {ln['category_code']} 不存在")
            amt = int(ln["amount"])
            # B 類的正負號代表錢的方向：收進來（借款撥入、押金退回）記負數
            if cat.nature == "balance" and doc_type == "receivable":
                amt = -abs(amt)
            lines.append(LineInput(ln.get("period_key", period), ln["category_code"], amt, ln.get("store_id"),
                                   it.get("bank_memo")))
        doc = self.posting.create(DocumentInput(
            doc_type=doc_type, company_id=it["company_id"], doc_date=date.fromisoformat(str(it["txn_date"])),
            source_kind="bank_only", source_ref=it["cash_txn_id"], counterparty_name=payload.get("counterparty_name"),
            settle_amount=int(it["amount"]), memo=payload.get("memo") or it.get("bank_memo"), lines=lines,
            asset_life_months=payload.get("asset_life_months")), actor)
        self.docs.add_settlement(Settlement(document_id=doc.id, cash_txn_id=it["cash_txn_id"],
                                            cash_account_id=it["cash_account_id"],
                                            txn_date=date.fromisoformat(str(it["txn_date"])),
                                            amount=int(it["amount"]), bank_amount=int(it["amount"]),
                                            match_level="manual"))
        self.s.refresh(doc)
        self.posting.recompute_settlement(doc)
        return doc


def _input_from_json(d: dict) -> DocumentInput:
    d = dict(d)
    d["doc_date"] = date.fromisoformat(d["doc_date"])
    d["lines"] = [LineInput(**ln) for ln in d["lines"]]
    for k in ("due_date", "expected_delivery_date"):
        if d.get(k):
            d[k] = date.fromisoformat(d[k])
    for k in ("counterparty_id", "accrual_template_id", "related_document_id", "import_batch_id"):
        if d.get(k):
            d[k] = uuid.UUID(str(d[k]))
    return DocumentInput(**d)


def input_to_json(inp: DocumentInput) -> dict:
    out = {k: v for k, v in inp.__dict__.items() if v is not None}
    out["doc_date"] = inp.doc_date.isoformat()
    out["lines"] = [ln.__dict__ for ln in inp.lines]
    for k in ("counterparty_id", "accrual_template_id", "related_document_id", "import_batch_id"):
        if getattr(inp, k):
            out[k] = str(getattr(inp, k))
    for k in ("due_date", "expected_delivery_date"):
        if getattr(inp, k):
            out[k] = getattr(inp, k).isoformat()
    return out
