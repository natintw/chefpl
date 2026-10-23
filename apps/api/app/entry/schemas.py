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
    # 帳戶資料（以現金流系統為準；有帶就更新帳務系統的帳戶主檔）
    cash_account_company_id: str | None = None
    cash_account_bank: str | None = None
    cash_account_name: str | None = None
    cash_account_last4: str | None = None


class BankTxnRef(BaseModel):
    cash_txn_id: str = Field(min_length=1, max_length=200)


class BankTxnWebhookIn(BaseModel):
    """imported：新匯入的交易（要比對）；deleted：現金流系統刪掉的交易（撤回先前的比對結果）；
    status：查詢交易目前的核對狀態（唯讀，現金流系統的「帳務核對」頁用）。"""
    event: Literal["bank_transactions.imported", "bank_transactions.deleted",
                   "bank_transactions.status"] = "bank_transactions.imported"
    items: list[dict] = Field(min_length=1, max_length=1000)

    def imported(self) -> list[BankTxnIn]:
        return [BankTxnIn.model_validate(i) for i in self.items]

    def deleted(self) -> list[BankTxnRef]:
        return [BankTxnRef.model_validate(i) for i in self.items]


class BankOnlyIn(BaseModel):
    items: list[BankOnlyItemIn] = Field(min_length=1, max_length=500)


class CashPaymentIn(BaseModel):
    """現金付款：選好的單據標成已付。來源：petty_cash 零用金、store_cash 門市收銀、advance 老闆／員工代墊。"""
    document_ids: list[str] = Field(min_length=1, max_length=200)
    paid_date: date
    source: Literal["petty_cash", "store_cash", "advance"] = "petty_cash"
    note: str | None = Field(default=None, max_length=200)
    amount: int | None = Field(default=None, gt=0)
