"""請求內容（對應 OpenAPI 的 DocumentCreate 等）。"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.feature.common import DocumentInput, LineInput


class LineIn(BaseModel):
    period_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    category_code: str
    amount: int
    store_id: str | None = None
    description: str | None = None


class DocumentCreate(BaseModel):
    doc_type: Literal["payable", "receivable", "accrual", "advance_receipt"]
    company_id: str
    doc_date: date
    lines: list[LineIn] = []
    counterparty_id: uuid.UUID | None = None
    counterparty_name: str | None = None
    invoice_no: str | None = None
    has_invoice: bool = False
    settle_amount: int | None = None
    tax_amount: int = 0
    settlement_method: Literal["pending", "bank_paid", "petty_cash"] = "pending"
    due_date: date | None = None
    memo: str | None = None
    accrual_mode: Literal["estimate_reverse", "provision"] | None = None
    expected_delivery_date: date | None = None
    asset_life_months: int | None = Field(default=None, ge=1, le=600)

    def to_input(self) -> DocumentInput:
        return DocumentInput(
            doc_type=self.doc_type, company_id=self.company_id, doc_date=self.doc_date,
            lines=[LineInput(**l.model_dump()) for l in self.lines], source_kind="manual",
            counterparty_id=self.counterparty_id, counterparty_name=self.counterparty_name,
            invoice_nos=[self.invoice_no] if self.invoice_no else [], has_invoice=self.has_invoice,
            tax_amount=self.tax_amount, settle_amount=self.settle_amount, settlement_method=self.settlement_method,
            due_date=self.due_date, memo=self.memo, accrual_mode=self.accrual_mode,
            expected_delivery_date=self.expected_delivery_date, asset_life_months=self.asset_life_months)


class DocumentPatch(BaseModel):
    version: int
    memo: str | None = None
    due_date: date | None = None
    lines: list[LineIn] | None = None
    settle_amount: int | None = None
    tax_amount: int | None = None


class VoidIn(BaseModel):
    reason: str = Field(min_length=1)


class LinkAccrualIn(BaseModel):
    accrual_id: uuid.UUID
    relation: Literal["reverses_accrual", "consumes_provision"]


class AccrualBatchItem(BaseModel):
    template_id: uuid.UUID
    amount: int


class AccrualBatchIn(BaseModel):
    company_id: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    items: list[AccrualBatchItem]


class RecognizeItem(BaseModel):
    document_id: uuid.UUID
    amount: int
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    category_code: str
    store_id: str | None = None


class RecognizeIn(BaseModel):
    items: list[RecognizeItem]


class ResolveIn(BaseModel):
    action: str
    payload: dict = {}


class CloseIn(BaseModel):
    acknowledge_unallocated: bool = False


class ReopenIn(BaseModel):
    reason: str = Field(min_length=1)


class CounterpartyIn(BaseModel):
    name: str
    tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    default_category_code: str | None = None
    payment_rule: str = "next_month_30"
    keywords: list[str] = []
    is_intercompany: bool = False
    intercompany_company_id: str | None = None
    issues_invoice: bool = True


class CounterpartyPatch(BaseModel):
    default_category_code: str | None = None
    payment_rule: str | None = None
    keywords: list[str] | None = None
    is_active: bool | None = None


class RuleIn(BaseModel):
    layer: Literal["manual", "vendor_keyword", "global_keyword"]
    category_code: str
    counterparty_id: uuid.UUID | None = None
    keyword: str | None = None


class RecurringIn(BaseModel):
    company_id: str
    store_id: str | None = None
    counterparty_id: uuid.UUID | None = None
    category_code: str
    doc_type: Literal["payable", "journal"] = "payable"
    amount: int
    has_invoice: bool = False
    due_day: int | None = Field(default=None, ge=1, le=31)
    start_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    description: str


class AccrualTemplateIn(BaseModel):
    company_id: str
    store_id: str | None = None
    counterparty_id: uuid.UUID | None = None
    category_code: str
    accrual_mode: Literal["estimate_reverse", "provision"]
    estimate_method: Literal["avg_lookback", "fixed"] = "avg_lookback"
    fixed_amount: int | None = None
    start_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    description: str


# ----- integration（現金流系統）-----
class SettlementItemIn(BaseModel):
    document_id: str
    cash_txn_id: str
    cash_account_id: str
    txn_date: date
    amount: int = Field(ge=1)
    bank_amount: int | None = Field(default=None, ge=1)
    match_level: Literal["exact", "fee_diff", "partial", "over", "amount_only", "manual"]
    matched_by: Literal["auto", "manual"] = "auto"
    action: Literal["apply", "reverse"] = "apply"
    note: str | None = None


class SettlementsIn(BaseModel):
    items: list[SettlementItemIn] = Field(min_length=1, max_length=200)


class BankOnlyItemIn(BaseModel):
    cash_txn_id: str
    cash_account_id: str
    company_id: str
    txn_date: date
    direction: Literal["out", "in"]
    amount: int = Field(ge=1)
    bank_memo: str
    bank_note: str | None = None
    counterparty_name: str | None = None
    counterparty_account_no_masked: str | None = None
    counterparty_company_id: str | None = None
    suggested_kind: str | None = None
    suggested_category_code: str | None = None
    balance_after: int | None = None


class BankTxnIn(BaseModel):
    """webhook：現金流系統送來的一筆銀行交易（只送事實，比對由帳務系統做）。"""
    cash_txn_id: str = Field(min_length=1, max_length=200)
    cash_account_id: str
    txn_date: date
    direction: Literal["out", "in"]
    amount: int = Field(ge=1)
    bank_memo: str | None = None
    bank_note: str | None = None
    counterparty_name: str | None = None
    counterparty_account_no: str | None = Field(None, description="對方帳號，可遮蔽，至少保留末 5 碼")


class BankTxnWebhookIn(BaseModel):
    event: Literal["bank_transactions.imported"] = "bank_transactions.imported"
    items: list[BankTxnIn] = Field(min_length=1, max_length=1000)


class BankOnlyIn(BaseModel):
    items: list[BankOnlyItemIn] = Field(min_length=1, max_length=500)
