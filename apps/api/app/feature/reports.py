"""損益表、總覽、下鑽、B 類餘額（開發文件 6 節）。

金額一律未稅。損益只來自 v_pl_lines（已排除 B 類）；餘額只來自 v_balance_lines。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.feature.repositories import CatalogRepository, DocumentRepository, OpsRepository
from app.guard.principals import Scope
from app.seams.company import get_directory
from app.seams.invoice_feed import get_invoice_feed

SECTION_ORDER = ["revenue", "cogs", "labor", "occupancy", "opex", "channel", "allocation", "non_operating"]
SECTION_LABEL = {"revenue": "營業收入", "cogs": "銷貨成本", "labor": "人事費用", "occupancy": "占用成本",
                 "opex": "營運費用", "channel": "通路費用", "allocation": "總部分攤", "non_operating": "營業外"}
UNALLOCATED = "__company__:"


def _ratio(a: float, b: float) -> float | None:
    return round(a / b * 100, 1) if b else None


class _Col:
    """一欄的累計：各類別金額與各區塊小計。"""

    def __init__(self) -> None:
        self.cat: dict[str, int] = defaultdict(int)
        self.sec: dict[str, int] = defaultdict(int)
        self.ic_rev = 0
        self.ic_cogs = 0

    def merge(self, other: "_Col") -> None:
        for k, v in other.sec.items():
            self.sec[k] += v
        for k, v in other.cat.items():
            self.cat[k] = self.cat.get(k, 0) + v

    def add(self, row: dict[str, Any]) -> None:
        amt = int(row["amount"])
        # 營業外同一區塊有收入也有支出：收入記正、支出記負，小計即淨額；其他區塊一律記正數
        signed = -amt if (row["pl_section"] == "non_operating" and row["nature"] == "expense") else amt
        self.cat[row["category_code"]] += signed
        self.sec[row["pl_section"]] += signed
        if row["is_intercompany"]:
            if row["category_code"] == "R06":
                self.ic_rev += amt
            elif row["category_code"] == "C99":
                self.ic_cogs += amt

    @property
    def revenue(self) -> int: return self.sec["revenue"]
    @property
    def gross(self) -> int: return self.revenue - self.sec["cogs"]
    @property
    def store_op(self) -> int:
        return self.gross - self.sec["labor"] - self.sec["occupancy"] - self.sec["opex"] - self.sec["channel"]
    @property
    def store_net(self) -> int: return self.store_op - self.sec["allocation"]
    @property
    def net(self) -> int: return self.store_net + self.sec["non_operating"]

    def kpis(self) -> dict[str, float | None]:
        r = self.revenue
        return {"food_cost_pct": _ratio(self.sec["cogs"], r), "labor_cost_pct": _ratio(self.sec["labor"], r),
                "prime_cost_pct": _ratio(self.sec["cogs"] + self.sec["labor"], r),
                "occupancy_pct": _ratio(self.sec["occupancy"], r), "store_margin_pct": _ratio(self.store_op, r)}


class ReportService:
    def __init__(self, session: Session):
        self.s = session
        self.docs = DocumentRepository(session)
        self.catalog = CatalogRepository(session)
        self.ops = OpsRepository(session)
        self.dir = get_directory()

    def _company_ids(self, scope: Scope, company_id: str | None) -> set[str] | None:
        """回傳要查的公司（含分公司）；None 表示全部。"""
        if company_id:
            ids = {c.id for c in self.dir.list_companies()
                   if c.id == company_id or c.parent_company_id == company_id}
            return ids if scope.all or ids & scope.companies else set()
        if scope.all:
            return None
        return set(scope.companies) | {s.company_id for s in self.dir.list_stores() if s.id in scope.stores}

    def _label(self, key: str, group_by: str) -> str:
        if group_by == "store":
            if key.startswith(UNALLOCATED):
                c = self.dir.get_company(key[len(UNALLOCATED):])
                return f"{c.name if c else key}｜公司層級（未分攤）"
            st = self.dir.get_store(key)
            return st.name if st else key
        if group_by == "company":
            c = self.dir.get_company(key)
            return c.name if c else key
        return key

    # ------------------------------------------------------------------ 損益表
    def statement(self, scope: Scope, *, period_from: str, period_to: str, company_id: str | None = None,
                  store_id: str | None = None, columns: str = "store",
                  eliminate_intercompany: bool | None = None) -> dict[str, Any]:
        cids = self._company_ids(scope, company_id)
        if scope.store_only and not store_id:
            raise ValueError("store scope requires store_id")
        rows = self.docs.pl_aggregate(company_ids=cids, store_id=store_id, period_from=period_from,
                                      period_to=period_to, group_by=columns)
        group_level = company_id is None and store_id is None
        eliminate = group_level if eliminate_intercompany is None else eliminate_intercompany

        cols: dict[str, _Col] = defaultdict(_Col)
        total = _Col()
        for r in rows:
            if eliminate and r["is_intercompany"]:
                total.ic_rev += int(r["amount"]) if r["category_code"] == "R06" else 0
                total.ic_cogs += int(r["amount"]) if r["category_code"] == "C99" else 0
                continue
            cols[r["col"]].add(r)
            total.add(r)

        keys = sorted(cols, key=lambda k: (k.startswith(UNALLOCATED), self._label(k, columns)))
        col_list = [{"key": k, "label": self._label(k, columns)} for k in keys] + [{"key": "total", "label": "合計"}]
        allc = {**cols, "total": total}

        sections = []
        for sec in SECTION_ORDER:
            cats = [c for c in self.catalog.categories.values() if c.pl_section == sec]
            lines = [{"category_code": c.code, "label": c.name,
                      "values": {k: allc[k].cat.get(c.code, 0) for k in allc}}
                     for c in cats if any(allc[k].cat.get(c.code) for k in allc)]
            sections.append({"section": sec, "label": SECTION_LABEL[sec], "lines": lines,
                             "subtotal": {k: allc[k].sec.get(sec, 0) for k in allc}})
        subtotals = {
            "gross_profit": {k: v.gross for k, v in allc.items()},
            "store_operating_profit": {k: v.store_op for k, v in allc.items()},
            "store_net_profit": {k: v.store_net for k, v in allc.items()},
            "net_profit": {k: v.net for k, v in allc.items()},
        }
        return {
            "period_from": period_from, "period_to": period_to, "columns": col_list, "sections": sections,
            "subtotals": subtotals, "kpis": {k: v.kpis() for k, v in allc.items()},
            "intercompany_eliminated": {"revenue": total.ic_rev, "cogs": total.ic_cogs} if eliminate else None,
            "intercompany_unmatched": (total.ic_rev - total.ic_cogs) if eliminate else None,
        }

    # ------------------------------------------------------------------ 總覽
    def overview(self, scope: Scope, period: str) -> dict[str, Any]:
        cids = self._company_ids(scope, None)
        rows = self.docs.pl_aggregate(company_ids=cids, store_id=None, period_from=period, period_to=period,
                                      group_by="store")
        per: dict[str, _Col] = defaultdict(_Col)
        group = _Col()
        for r in rows:
            per[r["col"]].add(r)
            if not r["is_intercompany"]:
                group.add(r)
            else:
                group.ic_rev += int(r["amount"]) if r["category_code"] == "R06" else 0
                group.ic_cogs += int(r["amount"]) if r["category_code"] == "C99" else 0

        companies = [c for c in self.dir.list_companies() if cids is None or c.id in cids]
        accrual_by_company = self._accrual_amounts(period, cids)
        adv = self.docs.advance_balance(cids)
        out_rows, unallocated, company_rows = [], 0, []
        subs: dict[str, _Col] = {}
        for c in companies:
            sub = _Col()  # 公司小計＝旗下門市＋公司層級（未分攤），集團內交易在公司層級不沖銷
            for st in self.dir.list_stores(c.id):
                if not scope.allows_store(c.id, st.id):
                    continue
                col = per.get(st.id, _Col())
                sub.merge(col)
                out_rows.append({"company": {"id": c.id, "name": c.name}, "store": {"id": st.id, "name": st.name},
                                 "revenue": col.revenue, "store_operating_profit": col.store_op, **col.kpis(),
                                 "completeness": self._completeness(c.id, st.id, period)})
            stores_op = sub.store_op
            un = per.get(UNALLOCATED + c.id)
            un_amount = -un.store_op if un is not None else 0
            if un is not None:
                unallocated += un_amount
                sub.merge(un)
            if scope.store_only:  # 只看得到門市的人（店長）不顯示公司小計
                continue
            subs[c.id] = sub
            company_rows.append({"id": c.id, "name": c.name, "parent_company_id": c.parent_company_id,
                                 "revenue": sub.revenue, "stores_operating_profit": stores_op,
                                 "unallocated_amount": un_amount, "operating_profit": sub.store_op,
                                 "net_profit": sub.net, **sub.kpis()})
        # 法人合計：總公司＋旗下分公司（同一統編、同一法人），只有帶分公司的總公司才有
        entities = []
        for parent in companies:
            if parent.parent_company_id or parent.id not in subs:
                continue
            branches = [c for c in companies if c.parent_company_id == parent.id and c.id in subs]
            if not branches:
                continue
            ent = _Col()
            for cid in [parent.id] + [b.id for b in branches]:
                ent.merge(subs[cid])
            entities.append({"id": parent.id, "name": parent.name, "company_ids": [parent.id] + [b.id for b in branches],
                             "revenue": ent.revenue, "operating_profit": ent.store_op, "net_profit": ent.net,
                             **ent.kpis()})
        retail = [r for r in out_rows if r["store"]["id"] in per]
        return {
            "period": period,
            "totals": {"revenue": group.revenue, "gross_profit": group.gross,
                       "store_operating_profit": group.store_op, "net_profit": group.net, **group.kpis()},
            "rows": out_rows,
            "companies": company_rows,
            "entities": entities,
            "unallocated_amount": unallocated,
            "intercompany_unmatched": group.ic_rev - group.ic_cogs,
            "accrual_amount": sum(accrual_by_company.values()),
            "advance_balance": sum(adv.values()),
            "stores_with_data": len(retail),
        }

    def _accrual_amounts(self, period: str, cids: set[str] | None) -> dict[str, int]:
        from sqlalchemy import func, select

        from app.feature.models import Document, DocumentLine
        stmt = (select(DocumentLine.company_id, func.sum(DocumentLine.amount)).join(Document)
                .where(Document.doc_type == "accrual", Document.status == "posted", DocumentLine.period_key == period)
                .group_by(DocumentLine.company_id))
        if cids is not None:
            stmt = stmt.where(DocumentLine.company_id.in_(cids))
        return {c: int(v) for c, v in self.s.execute(stmt)}

    def _completeness(self, company_id: str, store_id: str, period: str) -> dict[str, Any]:
        payroll = self.docs.has_lines(company_id=company_id, period=period, source_kind="payroll", store_id=store_id)
        reviews = self.ops.count_open_reviews(company_id, period)
        try:
            pending = get_invoice_feed().pending_count(company_id, period).total
            upstream_ok = True
        except Exception:  # noqa: BLE001 — 上游讀不到不影響總覽
            pending, upstream_ok = None, False
        missing = []
        if not payroll:
            missing.append("薪資未匯入")
        if reviews:
            missing.append(f"待確認 {reviews} 件")
        if pending:
            missing.append(f"發票待複核 {pending} 張")
        return {"level": "complete" if not missing else "partial", "payroll": "imported" if payroll else "missing",
                "open_review_items": reviews, "pending_invoices": pending, "invoice_system_reachable": upstream_ok,
                "missing": missing}

    # ------------------------------------------------------------------ 下鑽
    def drilldown(self, scope: Scope, *, period: str, company_id: str | None, store_id: str | None,
                  category_code: str | None, pl_section: str | None, page: int = 1,
                  page_size: int = 50) -> list[dict[str, Any]]:
        cats = ([category_code] if category_code else
                [c.code for c in self.catalog.categories.values() if c.pl_section == pl_section])
        rows = self.docs.pl_lines(company_ids=self._company_ids(scope, company_id), store_id=store_id,
                                  period=period, category_codes=cats, limit=page_size,
                                  offset=(page - 1) * page_size)
        for r in rows:
            r["document_id"] = str(r["document_id"])
        return rows

    # ------------------------------------------------------------------ B 類餘額
    def balances(self, scope: Scope, *, as_of: date, company_id: str | None = None,
                 category_codes: list[str] | None = None) -> dict[str, Any]:
        cids = self._company_ids(scope, company_id)
        rows = self.docs.balance_aggregate(company_ids=cids, as_of=as_of)
        adv = self.docs.advance_balance(cids)
        names = {c.code: c.name for c in self.catalog.categories.values()}
        by_company: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in rows:
            if category_codes and r["category_code"] not in category_codes:
                continue
            slot = by_company[r["company_id"]].setdefault(r["category_code"], {
                "category_code": r["category_code"], "category_name": names[r["category_code"]],
                "amount": 0, "by_counterparty": []})
            slot["amount"] += int(r["amount"])
            if r["category_code"] in ("B02", "B05"):
                slot["by_counterparty"].append({
                    "counterparty_id": str(r["counterparty_id"]) if r["counterparty_id"] else None,
                    "counterparty_company_id": r["intercompany_company_id"],
                    "counterparty_name": r["counterparty_name"] or "（未指定對象）", "amount": int(r["amount"])})
        company_set = set(by_company) | set(adv)
        return {"as_of": as_of.isoformat(),
                "companies": [{"company_id": c, "balances": sorted(by_company.get(c, {}).values(),
                                                                   key=lambda x: x["category_code"]),
                               "advance_balance": adv.get(c, 0)} for c in sorted(company_set)]}
