"""守門層服務：使用者登入、系統金鑰、權限、稽核。

- 使用者（AUTH_MODE）
  - dev：讀 `X-Dev-User` 標頭（沒有就用 DEV_DEFAULT_USER），只在本機與測試使用
  - google：前端以 Google Sign-In 取得 ID token，放在 `Authorization: Bearer <id_token>`；
    這裡驗證簽章、audience、網域（ALLOWED_EMAIL_DOMAIN），再查 guard.users 取得角色
- 系統（/integration/*）：`Authorization: Bearer sk_…`，比對 INTEGRATION_CLIENTS 或 guard.service_clients 的 SHA-256
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Forbidden, Unauthenticated
from app.guard.models import AuditLog, ServiceClient, User, UserScope
from app.guard.principals import Scope, ServicePrincipal, UserPrincipal

PRIVATE_SUFFIX = ".railway.internal"


# ---------- 使用者 ----------
def _verify_google_id_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        from google.auth.transport import requests as g_requests  # 只有正式環境需要
        from google.oauth2 import id_token
    except ImportError as e:  # pragma: no cover
        raise Unauthenticated("伺服器未安裝 google-auth，無法驗證登入") from e
    try:
        info = id_token.verify_oauth2_token(token, g_requests.Request(), s.google_oauth_client_id)
    except Exception as e:  # noqa: BLE001
        raise Unauthenticated("登入已失效，請重新登入") from e
    if not info.get("email_verified") or not str(info.get("email", "")).endswith("@" + s.allowed_email_domain):
        raise Forbidden(f"只允許 {s.allowed_email_domain} 帳號")
    return info


def current_user(session: Session, authorization: str | None, dev_user: str | None) -> UserPrincipal:
    s = get_settings()
    if s.auth_mode == "dev":
        email, name = (dev_user or s.dev_default_user), (dev_user or s.dev_default_user).split("@")[0]
    elif s.auth_mode == "google":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise Unauthenticated("請先登入")
        info = _verify_google_id_token(authorization.split(" ", 1)[1])
        email, name = info["email"], info.get("name") or info["email"]
    else:
        raise Unauthenticated("AUTH_MODE 設定錯誤")

    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        admins = {a.strip() for a in s.bootstrap_admins.split(",") if a.strip()}
        if email not in admins:
            raise Forbidden("你還沒有這個系統的帳號，請洽管理者")
        user = User(email=email, name=name, role="admin")
        session.add(user)
        session.flush()
        session.add(UserScope(user_id=user.id, scope_kind="all", scope_id="*"))
        session.flush()
    if not user.is_active:
        raise Forbidden("帳號已停用")
    return UserPrincipal(email=user.email, name=user.name, role=user.role, scope=_scope_for(session, user))


def _scope_for(session: Session, user: User) -> Scope:
    rows = session.scalars(select(UserScope).where(UserScope.user_id == user.id)).all()
    if user.role in ("admin", "accountant") or any(r.scope_kind == "all" for r in rows):
        return Scope(all=True)
    return Scope(
        companies=frozenset(r.scope_id for r in rows if r.scope_kind == "company"),
        stores=frozenset(r.scope_id for r in rows if r.scope_kind == "store"),
    )


def require(principal: UserPrincipal, action: str) -> None:
    if not principal.can(action):
        raise Forbidden("你沒有執行這個動作的權限")


# ---------- 系統金鑰 ----------
@lru_cache
def _env_clients(raw: str) -> tuple[dict[str, Any], ...]:
    try:
        return tuple(json.loads(raw or "[]"))
    except json.JSONDecodeError:
        return ()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def current_service(session: Session, method: str, path: str, host: str, headers: dict[str, str],
                    body: bytes, signed_path: str | None = None) -> ServicePrincipal:
    """path 用於端點授權；signed_path（含查詢字串）用於公開網址模式的 HMAC 驗簽。"""
    s = get_settings()
    hostname = host.split(":")[0]
    if s.integration_mode == "private" and not hostname.endswith(PRIVATE_SUFFIX) and s.app_env not in ("local", "test"):
        raise Forbidden("integration 端點只接受 Railway 私有網路")

    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthenticated("缺少系統金鑰")
    digest = sha256_hex(auth.split(" ", 1)[1].strip())

    principal: ServicePrincipal | None = None
    for c in _env_clients(s.integration_clients):
        if any(hmac.compare_digest(digest, h) for h in c.get("key_sha256", [])):
            principal = ServicePrincipal(client=c["client"], allow=frozenset(c.get("allow", [])))
            break
    if principal is None:
        for c in session.scalars(select(ServiceClient).where(ServiceClient.is_active.is_(True))):
            if any(hmac.compare_digest(digest, h) for h in c.key_sha256):
                principal = ServicePrincipal(client=c.id, allow=frozenset(c.allow))
                break
    if principal is None:
        raise Unauthenticated("金鑰無效")

    if s.integration_mode == "public":
        _verify_hmac(s.integration_signing_secret, method, signed_path or path, headers, body)

    if not principal.can_call(method, path):
        raise Forbidden("這把金鑰不能呼叫這個端點")
    return principal


def _verify_hmac(secret: str, method: str, path_with_query: str, headers: dict[str, str], body: bytes) -> None:
    ts = headers.get("x-timestamp", "")
    sig = headers.get("x-signature", "")
    if not secret or not ts.isdigit() or not sig.startswith("v1="):
        raise Unauthenticated("缺少簽章")
    if abs(time.time() - int(ts)) > 300:
        raise Unauthenticated("簽章時間超過允許範圍")
    payload = f"{ts}\n{method.upper()}\n{path_with_query}\n{hashlib.sha256(body).hexdigest()}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig[3:]):
        raise Unauthenticated("簽章不符")


# ---------- 稽核 ----------
def audit(session: Session, actor: str, action: str, resource: str, before: dict | None = None,
          after: dict | None = None, ip: str | None = None) -> None:
    session.add(AuditLog(actor_email=actor, action=action, resource=resource, before=before, after=after, ip=ip))
