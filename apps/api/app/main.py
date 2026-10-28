"""FastAPI 進入點。啟動指令見 Dockerfile（HOST 預設 0.0.0.0；Railway 私有網路要設 HOST=::）。"""
from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.entry import integration, routes, routes_offsets
from app.errors import AppError

log = logging.getLogger("ledger")

app = FastAPI(title="帳務損益系統 API", version="0.1.0",
              description="合約：contracts/openapi.yaml（使用者端）、contracts/integration-cashflow.yaml（系統端）")
app.include_router(routes_offsets.router)  # 先註冊：/documents/lookup 要比 /documents/{doc_id} 先比對
app.include_router(routes.router)
app.include_router(integration.router)


@app.middleware("http")
async def proxy_key_gate(request: Request, call_next):
    """設了 FRONTEND_PROXY_KEY 時，使用者端 API 只接受前端代理轉進來的請求。/integration 與 /healthz 不受影響。"""
    key = get_settings().frontend_proxy_key
    if key and request.url.path.startswith("/api/"):
        if not hmac.compare_digest(request.headers.get("x-proxy-key", ""), key):
            return JSONResponse({"code": "UNAUTHENTICATED", "message": "請從帳務台進入"}, status_code=401)
    return await call_next(request)


@app.exception_handler(AppError)
async def app_error(_: Request, exc: AppError):
    return JSONResponse(exc.to_dict(), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse({"code": "VALIDATION_FAILED", "message": "欄位格式錯誤", "details": details}, status_code=422)


@app.exception_handler(Exception)
async def unexpected(_: Request, exc: Exception):  # 不把技術錯誤丟給使用者
    log.exception("unhandled error")
    return JSONResponse({"code": "INTERNAL", "message": "系統發生錯誤，請稍後再試"}, status_code=500)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
