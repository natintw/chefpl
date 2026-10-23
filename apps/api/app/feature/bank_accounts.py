"""銀行帳戶以現金流系統為準。

webhook 每筆交易帶上帳戶資料（cash_account_company_id、cash_account_bank、cash_account_name、cash_account_last4），
帳務系統存進 ledger.bank_accounts，再蓋過 fixtures 的同一個帳戶。Bill 在現金流系統改帳戶（例：換所屬公司、合併重複帳戶），
下一次送交易就跟上，不必再手動改帳務系統。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.feature.models import BankAccountRow
from app.seams.company import BankAccount, get_directory


def refresh_overlay(session: Session) -> None:
    """把資料庫裡現金流送來的帳戶套到主檔（每個請求開頭呼叫，多台主機也一致）。"""
    rows = session.scalars(select(BankAccountRow)).all()
    get_directory().set_bank_overlay([
        BankAccount(id=r.id, company_id=r.company_id, bank_name=r.bank_name or "", alias=r.alias or r.id,
                    last4=r.last4 or "") for r in rows])


def upsert_from_items(session: Session, items: list[dict[str, Any]]) -> dict[str, str]:
    """記下交易帶來的帳戶資料。回傳 {帳戶 ID: 錯誤訊息}（所屬公司不在主檔的帳戶）。"""
    d = get_directory()
    seen: dict[str, dict[str, Any]] = {}
    for it in items:
        if it.get("cash_account_company_id"):
            seen[it["cash_account_id"]] = it
    bad: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    for acct_id, it in seen.items():
        company_id = it["cash_account_company_id"]
        if d.get_company(company_id) is None:
            bad[acct_id] = f"帳戶 {acct_id} 的所屬公司 {company_id} 不在集團主檔"
            continue
        row = session.get(BankAccountRow, acct_id)
        if row is None:
            row = BankAccountRow(id=acct_id)
            session.add(row)
        row.company_id = company_id
        row.bank_name = it.get("cash_account_bank") or row.bank_name
        row.alias = it.get("cash_account_name") or row.alias
        row.last4 = it.get("cash_account_last4") or row.last4
        row.updated_at = now
    session.flush()
    refresh_overlay(session)
    return bad
