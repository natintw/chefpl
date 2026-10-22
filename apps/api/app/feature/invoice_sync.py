"""發票事件同步（開發文件 5.1）。由 worker 每 15 分鐘執行：讀事件 → 建立或更新應付 → 成功才前進游標。"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.feature.classification import classify
from app.feature.common import DocumentInput, LineInput, period_of
from app.feature.posting import PostingService
from app.feature.review import input_to_json
from app.seams.invoice_feed import CursorExpired, get_invoice_feed

CURSOR = "invoice_feed"
ACTOR = "system:invoice-sync"


class InvoiceSyncService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.ops, self.catalog = self.posting.docs, self.posting.ops, self.posting.catalog
        self.feed = get_invoice_feed()
        self.cfg = get_settings()

    def run(self, max_pages: int = 20) -> dict[str, int]:
        stats = {"events": 0, "created": 0, "updated": 0, "voided": 0, "review": 0, "skipped": 0}
        cursor = self.ops.cursor(CURSOR)
        for _ in range(max_pages):
            try:
                page = self.feed.list_changes(cursor, self.cfg.invoice_sync_batch)
            except CursorExpired:
                cursor = None  # 重新完整同步；以來源唯一鍵去重
                continue
            for ev in page.events:
                with self.s.begin_nested():
                    self._handle(ev, stats)
                stats["events"] += 1
            cursor = page.next_cursor
            self.ops.save_cursor(CURSOR, cursor)
            if not page.has_more:
                break
        return stats

    def _handle(self, ev: dict[str, Any], stats: dict[str, int]) -> None:
        inv = ev["invoice"]
        if inv.get("direction", "purchase") != "purchase":
            stats["skipped"] += 1
            return
        ref = inv["id"]
        existing = self.docs.get_by_source("invoice", ref)
        kind = ev["event_type"]
        if kind == "confirmed":
            if existing:
                stats["skipped"] += 1
                return
            self._create(inv, stats)
        elif kind == "updated":
            if existing is None:
                self._create(inv, stats)
            elif existing.settled_amount or any(self.ops.is_closed(existing.company_id, l.period_key)
                                                for l in existing.lines):
                self.ops.open_review("invoice_changed_after_settle", existing.company_id, f"document:{existing.id}",
                                     {"event": ev})
                stats["review"] += 1
            else:
                self._update_amounts(existing, inv)
                stats["updated"] += 1
        elif kind == "voided" and existing:
            try:
                self.posting.void(existing.id, f"發票系統作廢（{inv['invoice_no']}）", ACTOR)
                stats["voided"] += 1
            except AppError:
                self.ops.open_review("invoice_changed_after_settle", existing.company_id, f"document:{existing.id}",
                                     {"event": ev})
                stats["review"] += 1
        elif kind == "reopened" and existing and existing.status == "posted" and not existing.settled_amount:
            existing.status = "draft"
            existing.version += 1
            stats["updated"] += 1
        else:
            stats["skipped"] += 1

    def _build_input(self, inv: dict[str, Any]) -> tuple[DocumentInput, Any]:
        cp = self.posting.ensure_counterparty(inv["seller_name"], inv.get("seller_tax_id"), inv["company_id"], ACTOR)
        c = classify(self.catalog, cp, inv["seller_name"], "expense", inv["total_amount"])
        d = date.fromisoformat(inv["invoice_date"])
        inp = DocumentInput(
            doc_type="payable", company_id=inv["company_id"], doc_date=d, source_kind="invoice", source_ref=inv["id"],
            counterparty_id=cp.id, invoice_nos=[inv["invoice_no"]], has_invoice=True, tax_amount=inv["tax_amount"],
            settle_amount=inv["total_amount"], memo=f"發票 {inv['invoice_no']}",
            status="posted" if c.decided else "draft",
            lines=[LineInput(period_of(d), c.category_code or "B99", inv["sales_amount"], inv.get("store_id"),
                             inv["seller_name"])])
        return inp, c

    def _create(self, inv: dict[str, Any], stats: dict[str, int]) -> None:
        inp, c = self._build_input(inv)
        # 同一張發票已經從應付表或人工登打入帳：不新建，交給會計確認（開發文件 3.3）
        dup = self.docs.find_by_invoice(inv["invoice_no"], counterparty_id=inp.counterparty_id)
        if dup is not None:
            self.ops.open_review("duplicate_source", inv["company_id"], f"invoice:{inv['id']}",
                                 {"invoice": inv, "existing_document_id": str(dup.id),
                                  "pending_input": input_to_json(inp)}, period_key=inp.lines[0].period_key)
            stats["review"] += 1
            return
        doc = self.posting.create(inp, ACTOR)
        stats["created"] += 1
        if not c.decided:
            self.ops.open_review("unclassified", doc.company_id, f"document:{doc.id}",
                                 {"document_id": str(doc.id), "counterparty": inv["seller_name"],
                                  "suggestion": c.suggestion}, period_key=inp.lines[0].period_key)
            stats["review"] += 1

    def _update_amounts(self, doc, inv: dict[str, Any]) -> None:
        line = doc.lines[0]
        self.posting.update(doc.id, doc.version, ACTOR, settle_amount=inv["total_amount"], tax_amount=inv["tax_amount"],
                            lines=[LineInput(line.period_key, line.category_code, inv["sales_amount"], line.store_id,
                                             line.description)])


def _uuid(v: str) -> uuid.UUID:
    return uuid.UUID(v)
