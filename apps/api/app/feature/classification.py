"""分類規則引擎（《分類規則》3.1，開發文件 5.4）。命中就停：

0. 集團內：對象是集團內公司 → C99（支出）／R06（收入）
1. 人工指定（會計確認過的規則）
2. 廠商摘要關鍵字覆寫
3. 廠商預設類別
4. 全域摘要關鍵字（命中多個類別視為衝突，不採用）
5. AI 建議 → 不入帳，只回傳建議
6. 待確認
"""
from __future__ import annotations

from dataclasses import dataclass

from app.feature.models import Counterparty
from app.feature.repositories import CatalogRepository
from app.seams.ai import suggest_if_confident


@dataclass
class Classification:
    category_code: str | None
    layer: str  # intercompany / manual / vendor_keyword / vendor_default / global_keyword / ai / none
    suggestion: dict | None = None

    @property
    def decided(self) -> bool:
        return self.category_code is not None


def classify(catalog: CatalogRepository, counterparty: Counterparty | None, description: str | None,
             direction: str = "expense", amount: int = 0, use_ai: bool = True) -> Classification:
    desc = description or ""
    if counterparty is not None:
        if counterparty.is_intercompany:
            return Classification("C99" if direction == "expense" else "R06", "intercompany")
        for r in catalog.rules("manual", counterparty.id):
            if not r.keyword or r.keyword in desc:
                return Classification(r.category_code, "manual")
        for r in catalog.rules("vendor_keyword", counterparty.id):
            if r.keyword and r.keyword in desc:
                return Classification(r.category_code, "vendor_keyword")
        if counterparty.default_category_code:
            return Classification(counterparty.default_category_code, "vendor_default")

    wanted = {"expense", "balance"} if direction == "expense" else {"income", "balance"}
    hits = {r.category_code for r in catalog.rules("global_keyword") if r.keyword and r.keyword in desc
            and catalog.category(r.category_code) and catalog.category(r.category_code).nature in wanted}
    if len(hits) == 1:
        return Classification(hits.pop(), "global_keyword")

    if use_ai:
        sug = suggest_if_confident(counterparty.name if counterparty else "", desc, amount,
                                   catalog.candidates(wanted))
        if sug:
            return Classification(None, "ai", sug)
    return Classification(None, "none")
