"""系統對系統 API（/integration/v1），合約：contracts/integration-cashflow.yaml。

所有 POST 都要帶 Idempotency-Key：同一把 key 重送回傳第一次的結果；同 key 不同內容回 409。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_ledger_session
from app.entry.deps import current_service
from app.entry.schemas import BankOnlyIn, SettlementsIn
from app.errors import IdempotencyConflict, ValidationFailed
from app.feature.reports import ReportService
from app.feature.repositories import OpsRepository
from app.feature.settlement import SettlementService
from app.guard.principals import ALL_SCOPE, ServicePrincipal

router = APIRouter(prefix="/integration/v1")


@router.get("/health")
def health():
    return {"status": "ok", "service": "ledger", "version": "0.1.0"}


@router.get("/open-items")
def open_items(company_id: str | None = None, doc_type: str | None = None, due_before: date | None = None,
               updated_after: datetime | None = None, cursor: str | None = None,
               limit: int = Query(200, ge=1, le=500), svc: ServicePrincipal = Depends(current_service),
               s: Session = Depends(get_ledger_session, scope="function")):
    types = [t for t in (doc_type or "").split(",") if t] or None
    return SettlementService(s).open_items(company_id=company_id, doc_types=types, due_before=due_before,
                                           updated_after=updated_after, cursor=cursor, limit=limit)


def _idempotent(s: Session, svc: ServicePrincipal, key: str, endpoint: str, body: bytes, handler):
    if not key or len(key) < 8:
        raise ValidationFailed("缺少 Idempotency-Key")
    digest = hashlib.sha256(body).hexdigest()
    ops = OpsRepository(s)
    prev = ops.idempotent_get(svc.client, key)
    if prev is not None:
        if prev.body_sha256 != digest:
            raise IdempotencyConflict("此 Idempotency-Key 已用於不同內容")
        return JSONResponse(prev.response_body, status_code=prev.response_status,
                            headers={"Idempotent-Replayed": "true"})
    result = handler()
    ops.idempotent_save(svc.client, key, endpoint, digest, 200, json.loads(json.dumps(result, default=str)))
    return result


@router.post("/settlements")
async def settlements(request: Request, body: SettlementsIn, idempotency_key: str = Header(...),
                      svc: ServicePrincipal = Depends(current_service), s: Session = Depends(get_ledger_session, scope="function")):
    raw = await request.body()
    items = [i.model_dump(mode="json") for i in body.items]
    return _idempotent(s, svc, idempotency_key, "settlements", raw,
                       lambda: SettlementService(s).apply(items, svc.actor))


@router.post("/bank-only-items")
async def bank_only(request: Request, body: BankOnlyIn, idempotency_key: str = Header(...),
                    svc: ServicePrincipal = Depends(current_service), s: Session = Depends(get_ledger_session, scope="function")):
    raw = await request.body()
    items = [i.model_dump(mode="json") for i in body.items]
    return _idempotent(s, svc, idempotency_key, "bank-only-items", raw,
                       lambda: SettlementService(s).bank_only(items))


@router.get("/balances")
def balances(as_of: date, company_id: str | None = None, category_code: str | None = None,
             svc: ServicePrincipal = Depends(current_service), s: Session = Depends(get_ledger_session, scope="function")):
    from app.config import get_settings
    cats = [c for c in (category_code or "").split(",") if c] or None
    out = ReportService(s).balances(ALL_SCOPE, as_of=as_of, company_id=company_id, category_codes=cats)
    out["opening_date"] = get_settings().opening_date
    return out
