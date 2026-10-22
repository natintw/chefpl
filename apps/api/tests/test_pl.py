"""損益表：區塊小計、KPI、集團內沖銷、B 類不進損益、下鑽、門市權限。"""
from tests.conftest import WH, WH_STORE
from tests.helpers import payable

BH = "comp-1"


def _receivable(client, company, store, period, amount, category="R01", counterparty="POS 日結"):
    r = client.post("/api/v1/documents", json={
        "doc_type": "receivable", "company_id": company, "doc_date": f"{period}-28", "counterparty_name": counterparty,
        "settle_amount": amount, "lines": [{"period_key": period, "category_code": category, "amount": amount,
                                             "store_id": store}]})
    assert r.status_code == 201, r.text
    return r.json()


def _seed(client):
    from app.db import session_scope
    from app.feature.repositories import CatalogRepository
    with session_scope() as s:
        CatalogRepository(s).add_counterparty(name="博竑食品", tax_id="42799631", is_intercompany=True,
                                              intercompany_company_id=BH, default_category_code="C99")
        CatalogRepository(s).add_counterparty(name="名勝文衡分公司", is_intercompany=True,
                                              intercompany_company_id=WH, default_category_code="R06")
    _receivable(client, WH, WH_STORE, "2026-08", 1_820_000)
    payable(client, amount=462_228, vendor="博竑食品", category="C99")            # 未稅 440,217
    payable(client, amount=77_399, vendor="協昌果菜行", category="C01")           # 未稅 73,713
    payable(client, amount=520_000, vendor="員工薪資", category="P01", has_invoice=False)
    # 博竑賣給文衡（集團內銷貨），工廠營收
    _receivable(client, BH, "store-bh-factory", "2026-08", 440_217, category="R06", counterparty="名勝文衡分公司")
    # B 類：還本 50,000 不進損益
    payable(client, amount=50_000, vendor="臺灣銀行", category="B02", has_invoice=False, store=None)


def test_store_statement(client):
    _seed(client)
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08",
                                                     "company_id": WH}).json()
    sec = {s["section"]: s["subtotal"]["total"] for s in st["sections"]}
    assert sec["revenue"] == 1_820_000
    assert sec["cogs"] == 440_217 + 73_713
    assert sec["labor"] == 520_000
    assert st["subtotals"]["gross_profit"]["total"] == 1_820_000 - 513_930
    assert st["kpis"]["total"]["food_cost_pct"] == round(513_930 / 1_820_000 * 100, 1)
    assert st["intercompany_eliminated"] is None          # 公司層級不沖銷
    assert all(l["category_code"] != "B02" for s in st["sections"] for l in s["lines"])


def test_group_statement_eliminates_intercompany(client):
    _seed(client)
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08",
                                                     "columns": "company"}).json()
    rev = next(s for s in st["sections"] if s["section"] == "revenue")["subtotal"]["total"]
    assert rev == 1_820_000                                # 博竑賣給文衡的 440,217 已沖銷
    assert st["intercompany_eliminated"] == {"revenue": 440_217, "cogs": 440_217}
    assert st["intercompany_unmatched"] == 0


def test_overview_and_drilldown(client):
    _seed(client)
    ov = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    wh = next(r for r in ov["rows"] if r["store"]["id"] == WH_STORE)
    assert wh["revenue"] == 1_820_000
    assert wh["completeness"]["payroll"] == "missing"     # 薪資不是從薪資表匯入
    dd = client.get("/api/v1/pl/drilldown", params={"period": "2026-08", "company_id": WH,
                                                     "pl_section": "cogs"}).json()["items"]
    assert {d["counterparty_name"] for d in dd} == {"博竑食品", "協昌果菜行"}


def test_store_manager_scope(client, session):
    from app.guard.models import User, UserScope
    _seed(client)
    u = User(email="wh@chefteng.com", name="文衡店長", role="store_manager")
    session.add(u)
    session.flush()
    session.add(UserScope(user_id=u.id, scope_kind="store", scope_id=WH_STORE))
    session.commit()
    h = {"X-Dev-User": "wh@chefteng.com"}
    assert client.get("/api/v1/pl/statement", headers=h, params={"period_from": "2026-08",
                                                                  "period_to": "2026-08"}).status_code == 403
    ok = client.get("/api/v1/pl/statement", headers=h, params={"period_from": "2026-08", "period_to": "2026-08",
                                                                "store_id": WH_STORE})
    assert ok.status_code == 200
    assert client.post("/api/v1/documents", headers=h, json={}).status_code in (403, 422)
