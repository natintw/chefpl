"""功能層共用：月份、到期日、金額方向、輸入資料結構。"""
from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(period: str, n: int) -> str:
    y, m = map(int, period.split("-"))
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_between(start: str, end: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def last_day(period: str) -> date:
    y, m = map(int, period.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])


def day_in_period(period: str, day: int) -> date:
    y, m = map(int, period.split("-"))
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def due_date_for(doc_date: date, rule: str) -> date | None:
    """付款條件：next_month_30（次月 30 日，遇小月取月底）、next_month_10、same_month_end、none。"""
    m = re.fullmatch(r"next_month_(\d{1,2})", rule or "")
    if m:
        return day_in_period(add_months(period_of(doc_date), 1), int(m.group(1)))
    if rule == "same_month_end":
        return last_day(period_of(doc_date))
    return None


def untaxed(total: int, has_invoice: bool, tax_rate: float) -> int:
    """《分類規則》第 2 節：有統一發票才除 1.05，四捨五入到元。"""
    return round(total / (1 + tax_rate)) if has_invoice else total


MULTI_MONTH_RE = re.compile(r"(\d{1,2})\s*[+＋、\-–~～至]\s*(\d{1,2})\s*月")


def months_in_memo(memo: str | None, doc_period: str) -> list[str] | None:
    """摘要含「7+8月」「7–8月」→ 回傳涵蓋的月份（依單據年度）；沒有就回傳 None。"""
    if not memo:
        return None
    m = MULTI_MONTH_RE.search(memo)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if not (1 <= a <= 12 and 1 <= b <= 12):
        return None
    year = int(doc_period[:4])
    if a > b:  # 例：12+1月 跨年
        return [f"{year:04d}-{a:02d}", f"{year + 1:04d}-{b:02d}"] if int(doc_period[5:]) >= a else \
               [f"{year - 1:04d}-{a:02d}", f"{year:04d}-{b:02d}"]
    return [f"{year:04d}-{mm:02d}" for mm in range(a, b + 1)]


def split_evenly(amount: int, parts: int) -> list[int]:
    base, rem = divmod(amount, parts)
    return [base + (1 if i < rem else 0) for i in range(parts)]


def outstanding(doc) -> int:
    """尚欠＝結清金額 − 銀行／零用金已付 − 沖抵（預付、折讓、他公司代付）。開發文件 3.8。"""
    return (doc.settle_amount or 0) - (doc.settled_amount or 0) - (getattr(doc, "offset_amount", 0) or 0)


# 單據方向：cash_out（我們付錢）、cash_in（我們收錢）
# prepayment：付訂金（錢流出，B07）；allowance 以「廠商要退錢」時才有結清金額（錢流入）
CASH_OUT_TYPES = {"payable", "prepayment"}
CASH_IN_TYPES = {"receivable", "advance_receipt"}
SETTLEABLE_TYPES = CASH_OUT_TYPES | CASH_IN_TYPES


def cash_effect(nature: str, amount: int, doc_type: str) -> int:
    """一行明細對結清金額的貢獻（未稅）。

    expense 正數＝花錢；income 正數＝收錢；balance 正數＝錢流出。
    payable：結清金額 = 費用 + B 類 − 收入；receivable：= 收入 − 費用 − B 類。
    """
    out = amount if nature in ("expense", "balance") else -amount
    return out if doc_type in CASH_OUT_TYPES else -out


@dataclass
class LineInput:
    period_key: str
    category_code: str
    amount: int
    store_id: str | None = None
    description: str | None = None
    counterparty_id: uuid.UUID | None = None       # B 類往來對象（代付的另一家公司、預付的廠商）
    original_document_id: uuid.UUID | None = None  # 折讓：這一行沖減的原單
    account_override_id: uuid.UUID | None = None   # 指定會計科目（代付抬頭為付款公司）


@dataclass
class DocumentInput:
    doc_type: str
    company_id: str
    doc_date: date
    lines: list[LineInput]
    source_kind: str = "manual"
    source_ref: str | None = None
    counterparty_id: uuid.UUID | None = None
    counterparty_name: str | None = None
    invoice_nos: list[str] = field(default_factory=list)
    has_invoice: bool = False
    tax_amount: int = 0
    settle_amount: int | None = None
    settlement_method: str = "pending"  # pending / bank_paid / petty_cash / none
    due_date: date | None = None
    memo: str | None = None
    accrual_mode: str | None = None
    accrual_template_id: uuid.UUID | None = None
    expected_delivery_date: date | None = None
    asset_life_months: int | None = None
    related_document_id: uuid.UUID | None = None
    relation: str | None = None
    import_batch_id: uuid.UUID | None = None
    status: str = "posted"
    allow_closed_period: bool = False  # 系統產生的開帳單據使用
    # 預付款
    expected_invoice_period: str | None = None
    planned_category_code: str | None = None
    order_total: int | None = None
    # 折讓
    original_invoice_no: str | None = None
    allowance_no: str | None = None
    allowance_reason: str | None = None
    refund_method: str | None = None
    # 代付
    on_behalf_of_company_id: str | None = None
    paid_by_company_id: str | None = None
    invoice_buyer_tax_id: str | None = None
    check_prepayments: bool = True   # 應付入帳時依賣方統編找未沖抵預付
