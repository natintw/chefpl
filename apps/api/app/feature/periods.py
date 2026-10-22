"""月結（開發文件 5.8）：檢查清單、關帳、重開。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ChecklistIncomplete, ValidationFailed
from app.feature.accruals import AccrualService
from app.feature.common import last_day
from app.feature.models import Document, DocumentLine
from app.feature.repositories import DocumentRepository, OpsRepository
from app.seams.company import get_directory
from app.seams.invoice_feed import get_invoice_feed

OPERATING_KINDS = {"restaurant", "counter"}


class PeriodService:
    def __init__(self, session: Session):
        self.s = session
        self.docs = DocumentRepository(session)
        self.ops = OpsRepository(session)
        self.dir = get_directory()

    def checklist(self, company_id: str, period: str, acknowledge_unallocated: bool = False) -> list[dict]:
        items: list[dict] = []

        def add(code: str, label: str, passed: bool | None, note: str = "") -> None:
            items.append({"code": code, "label": label, "status": "na" if passed is None else
                          ("passed" if passed else "failed"), "note": note})

        stores = [s for s in self.dir.list_stores(company_id) if s.is_active and s.kind in OPERATING_KINDS]
        no_rev = [s.name for s in stores if not self._has_revenue(company_id, period, s.id)]
        add("revenue", "營收已匯入", not no_rev if stores else None, f"缺：{'、'.join(no_rev)}" if no_rev else "")

        no_pay = [s.name for s in stores if not self.docs.has_lines(company_id=company_id, period=period,
                                                                     source_kind="payroll", store_id=s.id)]
        add("payroll", "薪資已匯入", not no_pay if stores else None, f"缺：{'、'.join(no_pay)}" if no_pay else "")

        missing_rec = [t.description for t in self.ops.recurring_for(period, company_id)
                       if not self.docs.get_by_source("recurring", f"{t.id}:{period}")]
        add("recurring", "固定費用已產生", not missing_rec, f"{len(missing_rec)} 筆未產生" if missing_rec else "")

        try:
            pc = get_invoice_feed().pending_count(company_id, period)
            add("invoices", "發票已同步", pc.total == 0, f"待處理 {pc.total} 張" if pc.total else "")
        except Exception:  # noqa: BLE001
            add("invoices", "發票已同步", False, "暫時讀不到發票系統")

        n = self.ops.count_open_reviews(company_id, period)
        add("reviews", "待確認已清空", n == 0, f"還有 {n} 件" if n else "")

        sug = AccrualService(self.s).suggestions(company_id, period)
        add("accruals", "預估已入帳", not sug, f"{len(sug)} 項費用沒有單據也沒有預估" if sug else "")

        due = [d for d in self.docs.list(company_ids={company_id}, doc_type="advance_receipt", status="posted",
                                         page_size=500)[0]
               if d.expected_delivery_date and d.expected_delivery_date <= last_day(period)
               and (d.settle_amount or 0) > d.recognized_amount]
        add("advances", "預收已轉營收", not due, f"{len(due)} 筆已到交貨日" if due else "")

        unalloc = self._unallocated(company_id, period)
        add("allocation", "公司層級已分攤", unalloc == 0 or acknowledge_unallocated,
            f"{unalloc:,} 元未分攤" + ("（已確認不分攤）" if acknowledge_unallocated and unalloc else "")
            if unalloc else "")

        b99 = self._sum(company_id, period, "B99")
        add("b99", "B99 已清空", b99 == 0, f"{b99:,} 元" if b99 else "")
        return items

    def _has_revenue(self, company_id: str, period: str, store_id: str) -> bool:
        stmt = (select(func.count()).select_from(DocumentLine).join(Document)
                .where(Document.status == "posted", DocumentLine.company_id == company_id,
                       DocumentLine.period_key == period, DocumentLine.store_id == store_id,
                       DocumentLine.category_code.like("R%")))
        return (self.s.scalar(stmt) or 0) > 0

    def _sum(self, company_id: str, period: str, category: str) -> int:
        stmt = (select(func.coalesce(func.sum(DocumentLine.amount), 0)).join(Document)
                .where(Document.status == "posted", DocumentLine.company_id == company_id,
                       DocumentLine.period_key == period, DocumentLine.category_code == category))
        return int(self.s.scalar(stmt) or 0)

    def _unallocated(self, company_id: str, period: str) -> int:
        stmt = (select(func.coalesce(func.sum(DocumentLine.amount), 0)).join(Document)
                .where(Document.status == "posted", DocumentLine.company_id == company_id,
                       DocumentLine.period_key == period, DocumentLine.store_id.is_(None),
                       ~DocumentLine.category_code.like("B%"), ~DocumentLine.category_code.like("R%"),
                       ~DocumentLine.category_code.like("N%")))
        return int(self.s.scalar(stmt) or 0)

    def close(self, company_id: str, period: str, actor: str, acknowledge_unallocated: bool = False) -> dict:
        items = self.checklist(company_id, period, acknowledge_unallocated)
        failed = [i for i in items if i["status"] == "failed"]
        if failed:
            raise ChecklistIncomplete(f"還有 {len(failed)} 項沒過",
                                      [{"field": i["code"], "message": f"{i['label']}：{i['note']}"} for i in failed])
        p = self.ops.period(company_id, period)
        p.status, p.closed_by, p.closed_at = "closed", actor, datetime.now(timezone.utc)
        return {"company_id": company_id, "period": period, "status": p.status, "checklist": items}

    def reopen(self, company_id: str, period: str, actor: str, reason: str) -> dict:
        if not reason:
            raise ValidationFailed("重開需填原因")
        p = self.ops.period(company_id, period)
        p.status, p.reopen_reason = "open", f"{actor}：{reason}"
        return {"company_id": company_id, "period": period, "status": p.status}
