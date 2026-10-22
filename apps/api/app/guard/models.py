"""守門層資料表（schema `guard`）。只有 guard/ 內的模組可以 import。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "guard"


class GuardBase(DeclarativeBase):
    pass


class User(GuardBase):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserScope(GuardBase):
    __tablename__ = "user_scopes"
    __table_args__ = {"schema": SCHEMA}
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True, default="*")


class ServiceClient(GuardBase):
    __tablename__ = "service_clients"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key_sha256: Mapped[list[str]] = mapped_column(ARRAY(Text))
    allow: Mapped[list[str]] = mapped_column(ARRAY(Text))
    signing_secret_ref: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(GuardBase):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_email: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
