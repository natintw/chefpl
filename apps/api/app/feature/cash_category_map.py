"""現金流分類 → 損益類別對照。

現金流系統（ChefCashFlow）匯入對帳單時已用「帳號 → 摘要 → 備註」規則把每筆交易分好類（進貨付款、薪資、水電雜支…），
webhook 會帶 cash_category_code／cash_category_name。這裡把它換成損益類別，當作待確認的預判：

  1. 認得出廠商、而且這家廠商在「廠商類別」設過類別 → 用廠商類別（最細）
  2. 否則用這張對照表：現金流分類 → 損益類別＋損益月份（薪資、勞健保預設上個月）
  3. 對照表沒設（票據支付、其他支出、未歸類…）→ 不預填，由會計選

現金流系統在設定頁自建的新分類，第一次送來時自動加進對照表（損益類別空白），會計在「廠商類別」頁補上即可。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ValidationFailed
from app.feature.models import CashCategoryMap, Counterparty, MgmtCategory


class CashCategoryMapService:
    def __init__(self, s: Session):
        self.s = s

    def rows(self) -> dict[str, CashCategoryMap]:
        return {r.cash_code: r for r in self.s.scalars(select(CashCategoryMap))}

    def list(self, open_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
        cats = {c.code: c for c in self.s.scalars(select(MgmtCategory))}
        out = []
        for r in sorted(self.rows().values(), key=_order):
            c = cats.get(r.ledger_category_code or "")
            out.append({"cash_code": r.cash_code, "cash_name": r.cash_name,
                        "ledger_category_code": r.ledger_category_code, "ledger_category_name": c.name if c else None,
                        "ledger_nature": c.nature if c else None, "period_mode": r.period_mode, "note": r.note,
                        "updated_by": r.updated_by, "open_reviews": (open_counts or {}).get(r.cash_code, 0)})
        return out

    def update(self, cash_code: str, ledger_category_code: str | None, period_mode: str | None, actor: str) -> dict[str, Any]:
        r = self.s.get(CashCategoryMap, cash_code)
        if r is None:
            raise ValidationFailed("找不到這個現金流分類", [{"field": "cash_code", "message": cash_code}])
        if ledger_category_code:
            if self.s.get(MgmtCategory, ledger_category_code) is None:
                raise ValidationFailed("損益類別不存在", [{"field": "ledger_category_code", "message": ledger_category_code}])
        if period_mode not in (None, "same", "prev"):
            raise ValidationFailed("損益月份只能是 same 或 prev", [{"field": "period_mode", "message": str(period_mode)}])
        r.ledger_category_code = ledger_category_code or None
        if period_mode:
            r.period_mode = period_mode
        r.updated_by = actor
        self.s.flush()
        return {"cash_code": r.cash_code, "ledger_category_code": r.ledger_category_code, "period_mode": r.period_mode}

    def register(self, items: list[dict[str, Any]]) -> None:
        """webhook 送來的現金流分類：沒看過的加進對照表（損益類別空白），名稱以現金流系統為準。"""
        seen = {(it.get("cash_category_code") or "").strip(): (it.get("cash_category_name") or "").strip()
                for it in items if (it.get("cash_category_code") or "").strip()}
        if not seen:
            return
        rows = self.rows()
        for code, name in seen.items():
            r = rows.get(code)
            if r is None:
                self.s.add(CashCategoryMap(cash_code=code, cash_name=name or code, ledger_category_code=None,
                                           period_mode="same", note="現金流系統新分類，請設定損益類別", updated_by="cashflow"))
            elif name and r.cash_name != name:
                r.cash_name = name
        self.s.flush()

    def suggest(self, items: list[tuple[str | None, str | None]]) -> list[dict[str, Any] | None]:
        """每筆（現金流分類代碼, 認出的廠商名稱）→ {category_code, period_mode, source} 或 None。"""
        rows = self.rows()
        names = {n for _, n in items if n}
        vendor_cat = {cp.name: cp.default_category_code
                      for cp in self.s.scalars(select(Counterparty).where(Counterparty.name.in_(names)))} if names else {}
        out: list[dict[str, Any] | None] = []
        for code, name in items:
            r = rows.get(code or "")
            mode = r.period_mode if r else "same"
            if name and vendor_cat.get(name):
                out.append({"category_code": vendor_cat[name], "period_mode": mode, "source": "vendor",
                            "source_label": f"廠商類別（{name}）"})
            elif r and r.ledger_category_code:
                out.append({"category_code": r.ledger_category_code, "period_mode": mode, "source": "cashflow",
                            "source_label": f"現金流分類「{r.cash_name}」"})
            else:
                out.append(None)
        return out


_ORDER = ["REV_", "EXP_", "FIN_", "CSH_", "INT_", "ADJ_", "UNCAT"]


def _order(r: CashCategoryMap) -> tuple[int, str]:
    for i, p in enumerate(_ORDER):
        if r.cash_code.startswith(p):
            return (i, r.cash_code)
    return (len(_ORDER), r.cash_code)
