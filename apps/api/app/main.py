"""FastAPI 進入點。Railway 啟動指令：uvicorn app.main:app --host :: --port $PORT"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.entry import integration, routes
from app.errors import AppError

log = logging.getLogger("ledger")

app = FastAPI(title="帳務損益系統 API", version="0.1.0",
              description="合約：contracts/openapi.yaml（使用者端）、contracts/integration-cashflow.yaml（系統端）")
app.include_router(routes.router)
app.include_router(integration.router)


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
