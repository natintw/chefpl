"""月結、待確認、匯入批次、範本、同步游標、冪等紀錄、排程紀錄。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.feature.models import (AccrualTemplate, ImportBatch, IntegrationRequest, JobRun, Period,
                                RecurringTemplate, ReviewItem, SyncCursor)


class OpsRepository:
    def __init__(self, session: Session):
        self.s = session

    # ----- 月份 -----
    def period(self, company_id: str, period_key: str) -> Period:
        self.__dict__.pop("_closed_memo", None)  # 拿去改狀態的話，記住的結果作廢
        p = self.s.get(Period, (company_id, period_key))
        if p is None:
            p = Period(company_id=company_id, period_key=period_key, status="open")
            self.s.add(p)
            self.s.flush()
        return p

    def is_closed(self, company_id: str, period_key: str) -> bool:
        # 同一個請求裡記住結果（沒有月結紀錄時 session.get 每次都會再查一次）；關帳／重開都走 period()，會更新這裡
        memo = self.__dict__.setdefault("_closed_memo", {})
        key = (company_id, period_key)
        if key not in memo:
            p = self.s.get(Period, key)
            memo[key] = p is not None and p.status == "closed"
        return memo[key]

    def list_periods(self, period_key: str) -> list[Period]:
        return list(self.s.scalars(select(Period).where(Period.period_key == period_key)))

    # ----- 待確認 -----
    def open_review(self, kind: str, company_id: str, ref: str | None, payload: dict,
                    period_key: str | None = None) -> ReviewItem | None:
        """同一 kind＋ref 只會有一筆未處理的；已存在就不重開。"""
        if ref is not None:
            existing = self.s.scalar(select(ReviewItem).where(ReviewItem.kind == kind, ReviewItem.ref == ref,
                                                              ReviewItem.status == "open"))
            if existing:
                return existing
        item = ReviewItem(kind=kind, company_id=company_id, ref=ref, payload=payload, period_key=period_key)
        self.s.add(item)
        self.s.flush()
        return item

    def review_exists(self, kind: str, ref: str) -> bool:
        return self.s.scalar(select(func.count()).select_from(ReviewItem).where(
            ReviewItem.kind == kind, ReviewItem.ref == ref)) > 0

    def review(self, item_id: uuid.UUID) -> ReviewItem | None:
        return self.s.get(ReviewItem, item_id)

    def list_reviews(self, *, company_ids: set[str] | None, kind: str | None, period: str | None,
                     status: str = "open") -> list[ReviewItem]:
        stmt = select(ReviewItem).where(ReviewItem.status == status)
        if company_ids is not None:
            stmt = stmt.where(ReviewItem.company_id.in_(company_ids))
        if kind:
            stmt = stmt.where(ReviewItem.kind == kind)
        if period:
            stmt = stmt.where(ReviewItem.period_key == period)
        return list(self.s.scalars(stmt.order_by(ReviewItem.created_at)))

    def count_open_reviews(self, company_id: str, period: str | None = None) -> int:
        stmt = select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "open",
                                                                  ReviewItem.company_id == company_id)
        if period:
            stmt = stmt.where((ReviewItem.period_key == period) | ReviewItem.period_key.is_(None))
        return self.s.scalar(stmt) or 0

    def close_reviews_for_ref(self, ref: str, resolution: dict, actor: str) -> None:
        for item in self.s.scalars(select(ReviewItem).where(ReviewItem.ref == ref, ReviewItem.status == "open")):
            item.status, item.resolution, item.resolved_by = "resolved", resolution, actor
            item.resolved_at = datetime.now(timezone.utc)

    # ----- 匯入批次 -----
    def add_batch(self, batch: ImportBatch) -> ImportBatch:
        self.s.add(batch)
        self.s.flush()
        return batch

    def batch(self, batch_id: uuid.UUID) -> ImportBatch | None:
        return self.s.get(ImportBatch, batch_id)

    def committed_batches(self, kind: str, company_id: str, period_key: str) -> list[ImportBatch]:
        return list(self.s.scalars(select(ImportBatch).where(
            ImportBatch.kind == kind, ImportBatch.company_id == company_id, ImportBatch.period_key == period_key,
            ImportBatch.status == "committed")))

    # ----- 範本 -----
    def add(self, obj):
        self.s.add(obj)
        self.s.flush()
        return obj

    def recurring_for(self, period: str, company_id: str | None = None) -> list[RecurringTemplate]:
        stmt = select(RecurringTemplate).where(
            RecurringTemplate.is_active.is_(True), RecurringTemplate.start_period <= period,
            (RecurringTemplate.end_period.is_(None)) | (RecurringTemplate.end_period >= period))
        if company_id:
            stmt = stmt.where(RecurringTemplate.company_id == company_id)
        return list(self.s.scalars(stmt))

    def accrual_templates_for(self, period: str, company_id: str | None = None,
                              mode: str | None = None) -> list[AccrualTemplate]:
        stmt = select(AccrualTemplate).where(
            AccrualTemplate.is_active.is_(True), AccrualTemplate.start_period <= period,
            (AccrualTemplate.end_period.is_(None)) | (AccrualTemplate.end_period >= period))
        if company_id:
            stmt = stmt.where(AccrualTemplate.company_id == company_id)
        if mode:
            stmt = stmt.where(AccrualTemplate.accrual_mode == mode)
        return list(self.s.scalars(stmt))

    def list_recurring(self, company_ids: set[str] | None) -> list[RecurringTemplate]:
        stmt = select(RecurringTemplate)
        if company_ids is not None:
            stmt = stmt.where(RecurringTemplate.company_id.in_(company_ids))
        return list(self.s.scalars(stmt))

    def list_accrual_templates(self, company_ids: set[str] | None) -> list[AccrualTemplate]:
        stmt = select(AccrualTemplate)
        if company_ids is not None:
            stmt = stmt.where(AccrualTemplate.company_id.in_(company_ids))
        return list(self.s.scalars(stmt))

    def accrual_template(self, tid: uuid.UUID) -> AccrualTemplate | None:
        return self.s.get(AccrualTemplate, tid)

    # ----- 同步游標 -----
    def cursor(self, name: str) -> str | None:
        c = self.s.get(SyncCursor, name)
        return c.cursor if c else None

    def save_cursor(self, name: str, value: str) -> None:
        self.s.execute(insert(SyncCursor).values(name=name, cursor=value)
                       .on_conflict_do_update(index_elements=["name"], set_={"cursor": value,
                                                                             "updated_at": func.now()}))

    # ----- 冪等 -----
    def idempotent_get(self, client: str, key: str) -> IntegrationRequest | None:
        return self.s.get(IntegrationRequest, (client, key))

    def idempotent_save(self, client: str, key: str, endpoint: str, body_sha256: str, status: int,
                        body: dict) -> None:
        self.s.add(IntegrationRequest(client=client, idempotency_key=key, endpoint=endpoint,
                                      body_sha256=body_sha256, response_status=status, response_body=body))
        self.s.flush()

    # ----- 排程紀錄 -----
    def job_start(self, job: str) -> JobRun:
        run = JobRun(job=job, started_at=datetime.now(timezone.utc), summary={})
        self.s.add(run)
        self.s.flush()
        return run

    def job_finish(self, run: JobRun, summary: dict) -> None:
        run.finished_at, run.summary = datetime.now(timezone.utc), summary
