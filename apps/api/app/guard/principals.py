"""守門層交給功能層的物件。功能層只認這些，不查 guard 的表。"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLE_ACTIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "accountant": {
        "pl.view", "balance.view", "doc.view", "doc.create", "doc.edit", "doc.void", "import.run",
        "review.resolve", "period.close", "settings.manage", "export.run",
    },
    "store_manager": {"pl.view", "doc.view"},
}


@dataclass(frozen=True)
class Scope:
    """可看哪些公司／門市。all=True 表示全部。"""
    all: bool = False
    companies: frozenset[str] = field(default_factory=frozenset)
    stores: frozenset[str] = field(default_factory=frozenset)

    def allows_company(self, company_id: str | None) -> bool:
        return self.all or (company_id is not None and company_id in self.companies)

    def allows_store(self, company_id: str, store_id: str | None) -> bool:
        if self.all or company_id in self.companies:
            return True
        return store_id is not None and store_id in self.stores

    @property
    def store_only(self) -> bool:
        return not self.all and not self.companies


@dataclass(frozen=True)
class UserPrincipal:
    email: str
    name: str
    role: str
    scope: Scope

    @property
    def actor(self) -> str:
        return self.email

    def can(self, action: str) -> bool:
        allowed = ROLE_ACTIONS.get(self.role, set())
        return "*" in allowed or action in allowed


@dataclass(frozen=True)
class ServicePrincipal:
    client: str
    allow: frozenset[str]

    @property
    def actor(self) -> str:
        return f"service:{self.client}"

    def can_call(self, method: str, path: str) -> bool:
        return f"{method.upper()} {path}" in self.allow


ALL_SCOPE = Scope(all=True)
