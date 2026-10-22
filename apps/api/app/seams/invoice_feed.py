"""InvoiceFeedConnector：讀發票系統的事件（合約：contracts/integration-invoice.yaml）。

- MockInvoiceFeed：讀 fixtures/invoice-events.json；cursor 為最後處理的 sequence
- HttpInvoiceFeed：呼叫 http://invoice.railway.internal:8080/api/v1/integration/*
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import get_settings
from app.errors import AppError, UpstreamUnavailable

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class CursorExpired(AppError):
    status, code = 410, "CURSOR_EXPIRED"


@dataclass
class EventPage:
    events: list[dict[str, Any]]
    next_cursor: str
    has_more: bool


@dataclass
class PendingCount:
    needs_review: int = 0
    archive_failed: int = 0
    unreadable_files: int = 0
    processing_files: int = 0

    @property
    def total(self) -> int:
        return self.needs_review + self.archive_failed + self.unreadable_files + self.processing_files


class InvoiceFeed(Protocol):
    def list_changes(self, cursor: str | None, limit: int) -> EventPage: ...
    def pending_count(self, company_id: str, month: str) -> PendingCount: ...
    def image_url(self, invoice_id: str) -> str | None: ...


@dataclass
class MockInvoiceFeed:
    events: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[tuple[str, str], PendingCount] = field(default_factory=dict)

    @classmethod
    def from_fixture(cls) -> MockInvoiceFeed:
        p = FIXTURES / "invoice-events.json"
        return cls(events=json.loads(p.read_text("utf-8")) if p.exists() else [])

    def list_changes(self, cursor: str | None, limit: int) -> EventPage:
        after = int(cursor or 0)
        page = [e for e in sorted(self.events, key=lambda e: e["sequence"]) if e["sequence"] > after][:limit]
        nxt = str(page[-1]["sequence"]) if page else str(after)
        has_more = any(e["sequence"] > int(nxt) for e in self.events)
        return EventPage(events=page, next_cursor=nxt, has_more=has_more)

    def pending_count(self, company_id: str, month: str) -> PendingCount:
        return self.pending.get((company_id, month), PendingCount())

    def image_url(self, invoice_id: str) -> str | None:
        return f"https://example.invalid/mock-invoice/{invoice_id}.jpg"


class HttpInvoiceFeed:
    def __init__(self) -> None:
        s = get_settings()
        self._client = httpx.Client(base_url=s.invoice_base_url, timeout=s.http_read_timeout_sec,
                                    headers={"Authorization": f"Bearer {s.invoice_api_key}"})

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise UpstreamUnavailable("暫時讀不到發票系統") from e
        if r.status_code == 410:
            raise CursorExpired("發票事件游標已過期")
        if r.status_code >= 500:
            raise UpstreamUnavailable("發票系統暫時無法服務")
        r.raise_for_status()
        return r.json()

    def list_changes(self, cursor: str | None, limit: int) -> EventPage:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = self._get("/api/v1/integration/invoice-events", params)
        return EventPage(events=body["events"], next_cursor=body["next_cursor"], has_more=body["has_more"])

    def pending_count(self, company_id: str, month: str) -> PendingCount:
        b = self._get("/api/v1/integration/pending-count", {"company_id": company_id, "month": month})
        return PendingCount(b["needs_review"], b["archive_failed"], b["unreadable_files"], b["processing_files"])

    def image_url(self, invoice_id: str) -> str | None:
        try:
            return self._get(f"/api/v1/integration/invoices/{invoice_id}/image", {})["url"]
        except httpx.HTTPStatusError:
            return None


_feed = None


def get_invoice_feed():
    global _feed
    if _feed is None:
        _feed = MockInvoiceFeed.from_fixture() if get_settings().use_mocks else HttpInvoiceFeed()
    return _feed


def set_invoice_feed(feed) -> None:
    """測試用：替換成自訂的 Mock。"""
    global _feed
    _feed = feed
