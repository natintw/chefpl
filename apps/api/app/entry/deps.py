"""入口層的 dependency：把守門層的結果交給路由。路由本身不做權限判斷以外的事。"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.db import get_guard_session
from app.guard import service as guard
from app.guard.principals import ServicePrincipal, UserPrincipal


def current_user(
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
    gs: Session = Depends(get_guard_session),
) -> UserPrincipal:
    return guard.current_user(gs, authorization, x_dev_user)


def need(action: str) -> Callable[..., UserPrincipal]:
    def dep(user: UserPrincipal = Depends(current_user)) -> UserPrincipal:
        guard.require(user, action)
        return user
    return dep


async def current_service(request: Request, gs: Session = Depends(get_guard_session)) -> ServicePrincipal:
    body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    principal = guard.current_service(gs, request.method, request.url.path, request.headers.get("host", ""),
                                      {k.lower(): v for k, v in request.headers.items()}, body, signed_path=path)
    guard.audit(gs, principal.actor, f"integration.{request.method.lower()}", request.url.path)
    return principal
