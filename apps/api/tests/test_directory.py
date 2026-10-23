"""主檔：門市 → 公司 → 集團（鄧師傅餐飲），以及匯入時門市名稱的寬鬆比對。"""
from app.feature.imports.service import store_core


def test_directory_matches_group_structure(client):
    d = client.get("/api/v1/directory").json()
    assert len(d["companies"]) == 7 and len(d["stores"]) == 12
    by_company = {}
    for s in d["stores"]:
        by_company.setdefault(s["company_id"], []).append(s["name"])
    assert sorted(by_company["comp-1"]) == sorted(["博竑食品", "巨蛋店", "大樂店", "漢神店", "義享店", "夢時代"])
    assert by_company["comp-1790263075210"] == ["漢神巨蛋店"]
    assert by_company["comp-borren"] == ["裕誠店"]
    branches = {c["name"]: c["parent_company_id"] for c in d["companies"] if c["parent_company_id"]}
    assert branches == {"博竑食品有限公司漢神分公司": "comp-1", "名勝食品文衡分公司": "comp-1790263124662"}


def test_store_name_variants():
    assert store_core("文衡店") == store_core("文衡門市") == "文衡"
    assert store_core("美術館") == store_core("美術館店")
    assert store_core("漢神店") != store_core("漢神巨蛋店")
    assert store_core("巨蛋店") != store_core("漢神巨蛋店")


def test_overview_company_subtotals(client):
    from tests.helpers import payable
    from tests.test_pl import _receivable
    _receivable(client, "comp-1790322196308", "store-ms-wenheng", "2026-08", 100_000)
    payable(client, company="comp-1790322196308", store="store-ms-wenheng", period="2026-08", amount=31_500, category="C01")
    payable(client, company="comp-1790322196308", store=None, period="2026-08", amount=10_500, category="E09",
            vendor="富霖印刷", invoice_no="ZZ00000001")
    d = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    wh = next(c for c in d["companies"] if c["id"] == "comp-1790322196308")
    assert wh["revenue"] == 100_000
    assert wh["stores_operating_profit"] == 100_000 - 30_000
    assert wh["unallocated_amount"] == 10_000
    assert wh["operating_profit"] == 100_000 - 30_000 - 10_000
    assert wh["parent_company_id"] == "comp-1790263124662"
