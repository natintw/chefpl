"""CompanyDirectory：公司、門市、銀行帳戶主檔（屬於現金流系統，開發文件 4.3）。

本系統不讀現金流系統的資料庫，只透過這個介面。
- MockCompanyDirectory：讀 fixtures/*.json
- CashflowApiCompanyDirectory：呼叫 cashflow 的 /integration/v1/companies、/stores、/bank-accounts（每小時快取）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.config import get_settings
from app.errors import UpstreamUnavailable

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass(frozen=True)
class Company:
    id: str
    code: str
    name: str
    tax_id: str | None
    parent_company_id: str | None
    is_active: bool = True


@dataclass(frozen=True)
class Store:
    id: str
    company_id: str
    code: str
    name: str
    kind: str = "restaurant"
    is_active: bool = True


@dataclass(frozen=True)
class BankAccount:
    id: str
    company_id: str
    bank_name: str
    alias: str
    last4: str
    is_active: bool = True
    merged_into: str | None = None


class CompanyDirectory(Protocol):
    def list_companies(self) -> list[Company]: ...
    def list_stores(self, company_id: str | None = None) -> list[Store]: ...
    def list_bank_accounts(self, company_id: str | None = None) -> list[BankAccount]: ...


class _Helpers:
    def get_company(self: CompanyDirectory, company_id: str) -> Company | None:
        return next((c for c in self.list_companies() if c.id == company_id), None)

    def get_by_tax_id(self: CompanyDirectory, tax_id: str) -> Company | None:
        return next((c for c in self.list_companies() if c.tax_id == tax_id), None)

    def get_store(self: CompanyDirectory, store_id: str) -> Store | None:
        return next((s for s in self.list_stores() if s.id == store_id), None)

    def get_bank_account(self: CompanyDirectory, account_id: str) -> BankAccount | None:
        return next((a for a in self.list_bank_accounts() if a.id == account_id), None)

    def legal_entity(self: CompanyDirectory, company_id: str) -> str:
        """分公司與總公司是同一個法人：回傳最上層公司 ID。"""
        seen, cur = set(), company_id
        while cur and cur not in seen:
            seen.add(cur)
            c = self.get_company(cur)  # type: ignore[attr-defined]
            if c is None or not c.parent_company_id:
                return cur
            cur = c.parent_company_id
        return cur

    def same_legal_entity(self, a: str, b: str) -> bool:
        return self.legal_entity(a) == self.legal_entity(b)  # type: ignore[attr-defined]


class MockCompanyDirectory(_Helpers):
    def __init__(self, fixtures: Path = FIXTURES):
        self._companies = [Company(**c) for c in json.loads((fixtures / "companies.json").read_text("utf-8"))]
        self._stores = [Store(**s) for s in json.loads((fixtures / "stores.json").read_text("utf-8"))]
        self._accounts = [BankAccount(**a) for a in json.loads((fixtures / "bank_accounts.json").read_text("utf-8"))]

    def list_companies(self) -> list[Company]:
        return list(self._companies)

    def list_stores(self, company_id: str | None = None) -> list[Store]:
        return [s for s in self._stores if company_id is None or s.company_id == company_id]

    def list_bank_accounts(self, company_id: str | None = None) -> list[BankAccount]:
        return [a for a in self._accounts if company_id is None or a.company_id == company_id]


class CashflowApiCompanyDirectory(_Helpers):
    TTL = 3600

    def __init__(self) -> None:
        s = get_settings()
        self._client = httpx.Client(base_url=s.cashflow_base_url, timeout=s.http_read_timeout_sec,
                                    headers={"Authorization": f"Bearer {s.cashflow_api_key}"})
        self._cache: dict[str, tuple[float, list]] = {}

    def _get(self, path: str, cls: type) -> list:
        hit = self._cache.get(path)
        if hit and time.time() - hit[0] < self.TTL:
            return hit[1]
        try:
            r = self._client.get(path, params={"include_inactive": "true"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            if hit:  # 讀不到時用舊快取，避免整個系統卡住
                return hit[1]
            raise UpstreamUnavailable("暫時讀不到現金流系統的主檔") from e
        fields = cls.__dataclass_fields__.keys()
        items = [cls(**{k: v for k, v in it.items() if k in fields}) for it in r.json()["items"]]
        self._cache[path] = (time.time(), items)
        return items

    def list_companies(self) -> list[Company]:
        return self._get("/integration/v1/companies", Company)

    def list_stores(self, company_id: str | None = None) -> list[Store]:
        return [s for s in self._get("/integration/v1/stores", Store) if company_id is None or s.company_id == company_id]

    def list_bank_accounts(self, company_id: str | None = None) -> list[BankAccount]:
        return [a for a in self._get("/integration/v1/bank-accounts", BankAccount)
                if company_id is None or a.company_id == company_id]


_directory = None


def get_directory():
    global _directory
    if _directory is None:
        s = get_settings()
        _directory = (MockCompanyDirectory() if s.use_mocks or s.company_directory_mode == "mock"
                      else CashflowApiCompanyDirectory())
    return _directory
