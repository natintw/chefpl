"""類別、對象、分類規則。"""
from __future__ import annotations

import uuid
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.feature.models import ClassificationRule, Counterparty, MgmtCategory


class CatalogRepository:
    def __init__(self, session: Session):
        self.s = session

    # ----- 類別 -----
    @cached_property
    def categories(self) -> dict[str, MgmtCategory]:
        return {c.code: c for c in self.s.scalars(select(MgmtCategory).order_by(MgmtCategory.sort_order))}

    def category(self, code: str) -> MgmtCategory | None:
        return self.categories.get(code)

    def candidates(self, natures: set[str] | None = None) -> list[dict]:
        return [{"category_code": c.code, "name": c.name} for c in self.categories.values()
                if c.is_active and (natures is None or c.nature in natures)]

    # ----- 對象 -----
    def counterparty(self, cid: uuid.UUID) -> Counterparty | None:
        return self.s.get(Counterparty, cid)

    def counterparty_by_tax_id(self, tax_id: str) -> Counterparty | None:
        return self.s.scalar(select(Counterparty).where(Counterparty.tax_id == tax_id))

    def counterparty_by_name(self, name: str) -> Counterparty | None:
        return self.s.scalar(select(Counterparty).where(Counterparty.name == name))

    def find_counterparty(self, name: str | None, tax_id: str | None) -> Counterparty | None:
        if tax_id:
            hit = self.counterparty_by_tax_id(tax_id)
            if hit:
                return hit
        if name:
            hit = self.counterparty_by_name(name)
            if hit:
                return hit
            # 付款關鍵字比對（例：「協昌」對到「協昌果菜行」）
            for cp in self.s.scalars(select(Counterparty).where(Counterparty.is_active.is_(True))):
                if any(k and k in name for k in cp.keywords):
                    return cp
        return None

    def list_counterparties(self, q: str | None = None) -> list[Counterparty]:
        stmt = select(Counterparty).order_by(Counterparty.name)
        if q:
            stmt = stmt.where(Counterparty.name.ilike(f"%{q}%"))
        return list(self.s.scalars(stmt))

    def add_counterparty(self, **kw) -> Counterparty:
        cp = Counterparty(**kw)
        self.s.add(cp)
        self.s.flush()
        return cp

    # ----- 規則 -----
    def rules(self, layer: str, counterparty_id: uuid.UUID | None = None) -> list[ClassificationRule]:
        stmt = select(ClassificationRule).where(ClassificationRule.layer == layer)
        if counterparty_id is not None:
            stmt = stmt.where(ClassificationRule.counterparty_id == counterparty_id)
        return list(self.s.scalars(stmt.order_by(ClassificationRule.created_at.desc())))

    def add_rule(self, layer: str, category_code: str, created_by: str, counterparty_id: uuid.UUID | None = None,
                 keyword: str | None = None) -> ClassificationRule:
        r = ClassificationRule(layer=layer, category_code=category_code, created_by=created_by,
                               counterparty_id=counterparty_id, keyword=keyword)
        self.s.add(r)
        self.s.flush()
        return r
