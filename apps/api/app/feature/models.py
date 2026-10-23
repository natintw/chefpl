"""功能層資料表（schema `ledger`）的 ORM 對應。

DDL 的唯一來源是 migrations/versions/0001_initial.py；這裡只做對應，不建表。
只有 feature/repositories/ 可以 import 這個模組。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (ARRAY, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String,
                        Text, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "ledger"


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MgmtCategory(Base):
    __tablename__ = "mgmt_categories"
    __table_args__ = {"schema": SCHEMA}
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    pl_section: Mapped[str] = mapped_column(Text)
    nature: Mapped[str] = mapped_column(Text)  # income / expense / balance
    sort_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CategoryAccountMap(Base):
    __tablename__ = "category_account_map"
    __table_args__ = {"schema": SCHEMA}
    category_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.accounts.id"))


class CashCategoryMap(Base):
    """現金流分類 → 損益類別（待確認的預設類別與損益月份）。"""
    __tablename__ = "cash_category_map"
    __table_args__ = {"schema": SCHEMA}
    cash_code: Mapped[str] = mapped_column(Text, primary_key=True)
    cash_name: Mapped[str] = mapped_column(Text)
    ledger_category_code: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    period_mode: Mapped[str] = mapped_column(Text, default="same")
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(Text, default="seed")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(Text)
    tax_id: Mapped[str | None] = mapped_column(String(8))
    id_masked: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=lambda: ["vendor"])
    is_intercompany: Mapped[bool] = mapped_column(Boolean, default=False)
    intercompany_company_id: Mapped[str | None] = mapped_column(Text)
    default_category_code: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    payment_rule: Mapped[str] = mapped_column(Text, default="next_month_30")
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # 同統編的其他寫法（發票辨識誤差）
    bank_accounts: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    default_store_id: Mapped[str | None] = mapped_column(Text)  # 這個對象的單據預設門市（例：員工屬於哪家店）  # 對方帳號（遮蔽或末幾碼），比對付款用
    issues_invoice: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClassificationRule(Base):
    __tablename__ = "classification_rules"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    layer: Mapped[str] = mapped_column(Text)  # manual / vendor_keyword / global_keyword
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.counterparties.id"))
    keyword: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    doc_type: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[str] = mapped_column(Text)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.counterparties.id"))
    doc_date: Mapped[date] = mapped_column(Date)
    invoice_nos: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    has_invoice: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    settle_amount: Mapped[int | None] = mapped_column(Integer)
    settled_amount: Mapped[int] = mapped_column(Integer, default=0)
    settle_status: Mapped[str] = mapped_column(Text, default="open")
    settled_by: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="draft")
    accrual_mode: Mapped[str | None] = mapped_column(Text)
    accrual_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    recognized_amount: Mapped[int] = mapped_column(Integer, default=0)
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id"))
    relation: Mapped[str | None] = mapped_column(Text)
    late_for_period: Mapped[str | None] = mapped_column(String(7))
    memo: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    lines: Mapped[list[DocumentLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentLine.line_no",
        lazy="selectin")
    settlements: Mapped[list[Settlement]] = relationship(back_populates="document", lazy="selectin")
    counterparty: Mapped[Counterparty | None] = relationship(lazy="joined")


class DocumentLine(Base):
    __tablename__ = "document_lines"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    company_id: Mapped[str] = mapped_column(Text)
    store_id: Mapped[str | None] = mapped_column(Text)
    period_key: Mapped[str] = mapped_column(String(7))
    category_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    amount: Mapped[int] = mapped_column(Integer)
    is_intercompany: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    document: Mapped[Document] = relationship(back_populates="lines")


class BankAccountRow(Base):
    """現金流系統的銀行帳戶（webhook 帶來的，以現金流為準）。"""
    __tablename__ = "bank_accounts"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(Text)
    bank_name: Mapped[str | None] = mapped_column(Text)
    alias: Mapped[str | None] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id"))
    cash_txn_id: Mapped[str] = mapped_column(Text)
    cash_account_id: Mapped[str] = mapped_column(Text)
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    bank_amount: Mapped[int | None] = mapped_column(Integer)
    match_level: Mapped[str] = mapped_column(Text)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    document: Mapped[Document] = relationship(back_populates="settlements")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    kind: Mapped[str] = mapped_column(Text)
    company_id: Mapped[str] = mapped_column(Text)
    period_key: Mapped[str | None] = mapped_column(String(7))
    file_name: Mapped[str] = mapped_column(Text)
    file_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    total_amount: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    errors: Mapped[list] = mapped_column(JSONB, default=list)
    parsed: Mapped[list] = mapped_column(JSONB, default=list)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    imported_by: Mapped[str] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecurringTemplate(Base):
    __tablename__ = "recurring_templates"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    company_id: Mapped[str] = mapped_column(Text)
    store_id: Mapped[str | None] = mapped_column(Text)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.counterparties.id"))
    category_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    doc_type: Mapped[str] = mapped_column(Text)
    offset_category_code: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id"))
    amount: Mapped[int] = mapped_column(Integer)
    last_amount: Mapped[int | None] = mapped_column(Integer)  # 折舊尾差放最後一期
    has_invoice: Mapped[bool] = mapped_column(Boolean, default=False)
    due_day: Mapped[int | None] = mapped_column(Integer)
    start_period: Mapped[str] = mapped_column(String(7))
    end_period: Mapped[str | None] = mapped_column(String(7))
    description: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccrualTemplate(Base):
    __tablename__ = "accrual_templates"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    company_id: Mapped[str] = mapped_column(Text)
    store_id: Mapped[str | None] = mapped_column(Text)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.counterparties.id"))
    category_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.mgmt_categories.code"))
    accrual_mode: Mapped[str] = mapped_column(Text)
    estimate_method: Mapped[str] = mapped_column(Text, default="avg_lookback")
    fixed_amount: Mapped[int | None] = mapped_column(Integer)
    start_period: Mapped[str] = mapped_column(String(7))
    end_period: Mapped[str | None] = mapped_column(String(7))
    description: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Period(Base):
    __tablename__ = "periods"
    __table_args__ = {"schema": SCHEMA}
    company_id: Mapped[str] = mapped_column(Text, primary_key=True)
    period_key: Mapped[str] = mapped_column(String(7), primary_key=True)
    status: Mapped[str] = mapped_column(Text, default="open")
    closed_by: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_reason: Mapped[str | None] = mapped_column(Text)


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _uuid()
    kind: Mapped[str] = mapped_column(Text)
    company_id: Mapped[str] = mapped_column(Text)
    period_key: Mapped[str | None] = mapped_column(String(7))
    ref: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(Text, default="open")
    resolution: Mapped[dict | None] = mapped_column(JSONB)
    resolved_by: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = {"schema": SCHEMA}
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    cursor: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class IntegrationRequest(Base):
    __tablename__ = "integration_requests"
    __table_args__ = {"schema": SCHEMA}
    client: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
