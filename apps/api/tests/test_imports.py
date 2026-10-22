"""應付表與薪資表匯入：成本合計分隔、跨月拆期、無發票、一格多張、身分證遮蔽、新廠商、集團內、重新匯入取代。"""
from tests.conftest import WH, WH_STORE
from tests.helpers import xlsx


def _seed_vendors():
    from app.db import session_scope
    from app.feature.repositories import CatalogRepository
    with session_scope() as s:
        cat = CatalogRepository(s)
        cat.add_counterparty(name="博竑食品", tax_id="42799631", is_intercompany=True, intercompany_company_id="comp-1",
                             default_category_code="C99", keywords=["博竑"])
        cat.add_counterparty(name="協昌果菜行", default_category_code="C01", keywords=["協昌"])
        cat.add_counterparty(name="美食家食材", default_category_code="C07", keywords=["美食家"])
        cat.add_counterparty(name="勝美行", default_category_code="C02", keywords=["勝美"])
        cat.add_counterparty(name="德昇會計師事務所", default_category_code="E08", keywords=["德昇"])
        cat.add_counterparty(name="香林環保", default_category_code="E05", keywords=["香林"])


AP_ROWS = [
    ["名勝文衡店 2026年8月 廠商應付帳款"],
    ["門市", "代號", "廠商", "發票號碼", "摘要", "應付金額"],
    ["文衡店", "42799631", "博竑食品", "AB11111111", "8月貨款", 462228],
    ["文衡店", "", "協昌", "AB22222222", "蔬菜", 41850],
    ["文衡店", "", "美食家食材", "AB33333333+AB33333334", "食材一批", 64648],
    ["文衡店", "", "勝美行", "沒發票", "五花肉片", 10260],
    ["文衡店", "A123456789", "雨來菇－張鈞", "", "雨來菇", 8400],
    ["", "", "成本合計", "", "", 587386],
    ["文衡店", "", "德昇會計師事務所", "AB44444444", "7+8月記帳費", 10000],
    ["文衡店", "", "香林環保", "AB55555555", "除油劑＋洗碗機租金", 3150],
    ["文衡店", "", "大同文具行", "", "影印紙", 500],
    ["", "", "費用合計", "", "", 13650],
]


def _upload(client, rows, kind="ap_sheet", company=WH, period="2026-08"):
    r = client.post("/api/v1/imports", data={"kind": kind, "company_id": company, "period": period},
                    files={"file": ("ap.xlsx", xlsx(rows), "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


def test_ap_sheet_import(client):
    _seed_vendors()
    b = _upload(client, AP_ROWS)
    assert b["errors"] == [] and b["row_count"] == 8
    assert b["total_amount"] == 587386 + 13650
    codes = {w["code"] for w in b["warnings"]}
    assert {"MULTI_INVOICE", "NOTED_NO_INVOICE", "NO_INVOICE", "ID_NUMBER", "NEW_COUNTERPARTY", "INTERCOMPANY",
            "MULTI_MONTH", "UNCLASSIFIED"} <= codes

    res = client.post(f"/api/v1/imports/{b['id']}/commit").json()
    assert res["created"] == 8 and res["drafts_for_review"] == 1          # 大同文具行：新廠商、規則都沒命中
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-07", "period_to": "2026-08",
                                                     "company_id": WH, "columns": "month"}).json()
    cogs = next(s for s in st["sections"] if s["section"] == "cogs")
    # 雨來菇是新廠商，但摘要「菇」命中全域關鍵字 C06；無發票者不除 1.05
    assert cogs["subtotal"]["2026-08"] == round(462228 / 1.05) + round(41850 / 1.05) + round(64648 / 1.05) \
        + 10260 + 8400
    review = client.get("/api/v1/review-items", params={"kind": "unclassified"}).json()["items"]
    assert [r["payload"]["counterparty"] for r in review] == ["大同文具行"]
    opex = next(s for s in st["sections"] if s["section"] == "opex")
    e08 = next(l for l in opex["lines"] if l["category_code"] == "E08")
    assert e08["values"]["2026-07"] == e08["values"]["2026-08"] == round(10000 / 1.05) // 2   # 7+8 月平均拆

    docs = client.get("/api/v1/documents", params={"company_id": WH, "source_kind": "ap_sheet",
                                                   "page_size": 100}).json()["items"]
    multi = next(d for d in docs if d["counterparty"]["name"] == "美食家食材")
    assert multi["invoice_nos"] == ["AB33333333", "AB33333334"]
    assert all(store == WH_STORE for d in docs for store in d["store_ids"])


def test_reimport_supersedes(client):
    _seed_vendors()
    rows = AP_ROWS[:4]
    b1 = _upload(client, rows)
    client.post(f"/api/v1/imports/{b1['id']}/commit")
    rows2 = rows[:3] + [["文衡店", "", "協昌", "AB22222222", "蔬菜", 42000]]
    b2 = _upload(client, rows2)
    res = client.post(f"/api/v1/imports/{b2['id']}/commit").json()
    assert res["superseded_batches"] == [b1["id"]]
    docs = client.get("/api/v1/documents", params={"company_id": WH, "page_size": 100}).json()["items"]
    assert sorted(d["settle_amount"] for d in docs) == [42000, 462228]


def test_bad_file_and_missing_header(client):
    r = client.post("/api/v1/imports", data={"kind": "ap_sheet", "company_id": WH, "period": "2026-08"},
                    files={"file": ("x.xlsx", b"not excel", "application/octet-stream")}).json()
    assert r["status"] == "failed" and r["errors"][0]["code"] == "UNREADABLE"
    r = _upload(client, [["a", "b"], [1, 2]])
    assert r["errors"][0]["code"] == "NO_HEADER"
    assert client.post(f"/api/v1/imports/{r['id']}/commit").json()["code"] == "HAS_ERRORS"


def test_payroll_import(client):
    rows = [["門市", "薪資", "加班與獎金", "勞保雇主", "健保雇主", "勞退"],
            ["文衡店", 412000, 38500, 30000, 17800, 21700],
            ["公司層級", 98000, 0, 7000, 4000, 5880]]
    b = _upload(client, rows, kind="payroll")
    assert b["errors"] == [] and b["total_amount"] == 520000 + 114880
    client.post(f"/api/v1/imports/{b['id']}/commit")
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08",
                                                     "company_id": WH}).json()
    labor = next(s for s in st["sections"] if s["section"] == "labor")
    assert labor["subtotal"][WH_STORE] == 520000
    ov = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    assert next(r for r in ov["rows"] if r["store"]["id"] == WH_STORE)["completeness"]["payroll"] == "imported"
    doc = client.get("/api/v1/documents", params={"source_kind": "payroll"}).json()["items"][0]
    assert doc["due_date"] == "2026-09-05"
