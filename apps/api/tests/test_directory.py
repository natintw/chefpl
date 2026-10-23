"""主檔：門市 → 公司 → 集團（鄧師傅餐飲），以及匯入時門市名稱的寬鬆比對。"""
from app.feature.imports.service import store_core


def test_directory_matches_group_structure(client):
    d = client.get("/api/v1/directory").json()
    assert len(d["companies"]) == 8 and len(d["stores"]) == 12
    by_company = {}
    for s in d["stores"]:
        by_company.setdefault(s["company_id"], []).append(s["name"])
    assert by_company["comp-1"] == ["博竑食品（工廠）"]
    assert sorted(by_company["comp-1790263075210"]) == sorted(["巨蛋店", "大樂店", "漢神店", "義享店", "夢時代"])
    assert by_company["comp-bh-hanshin"] == ["漢神巨蛋店"]
    assert by_company["comp-borren"] == ["裕誠店"]
    branches = {c["name"]: c["parent_company_id"] for c in d["companies"] if c["parent_company_id"]}
    assert branches == {"博竑食品有限公司高雄分公司": "comp-1", "博竑食品有限公司漢神分公司": "comp-1",
                        "名勝食品有限公司文衡分公司": "comp-1790263124662"}
    codes = {c["code"] for c in d["companies"]}
    assert codes == {"BH", "BHK", "BHH", "BR", "CT", "MS", "MSW", "YS"}
    # 門市代碼開頭就是所屬公司代碼
    code_of = {c["id"]: c["code"] for c in d["companies"]}
    assert all(s["code"].split("-")[0] == code_of[s["company_id"]] for s in d["stores"])


def test_store_name_variants():
    assert store_core("文衡店") == store_core("文衡門市") == "文衡"
    assert store_core("美術館") == store_core("美術館店")
    assert store_core("漢神店") != store_core("漢神巨蛋店")
    assert store_core("巨蛋店") != store_core("漢神巨蛋店")


def test_overview_company_subtotals(client):
    from tests.helpers import payable
    from tests.test_pl import _receivable
    _receivable(client, "comp-1790322196308", "store-msw-01", "2026-08", 100_000)
    payable(client, company="comp-1790322196308", store="store-msw-01", period="2026-08", amount=31_500, category="C01")
    payable(client, company="comp-1790322196308", store=None, period="2026-08", amount=10_500, category="E09",
            vendor="富霖印刷", invoice_no="ZZ00000001")
    d = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    wh = next(c for c in d["companies"] if c["id"] == "comp-1790322196308")
    assert wh["revenue"] == 100_000
    assert wh["stores_operating_profit"] == 100_000 - 30_000
    assert wh["unallocated_amount"] == 10_000
    assert wh["operating_profit"] == 100_000 - 30_000 - 10_000
    assert wh["parent_company_id"] == "comp-1790263124662"



def test_overview_entity_totals(client):
    from tests.test_pl import _receivable
    _receivable(client, "comp-1", "store-bh-factory", "2026-08", 50_000)
    _receivable(client, "comp-1790263075210", "store-bhk-dale", "2026-08", 30_000)
    _receivable(client, "comp-bh-hanshin", "store-bhh-judan", "2026-08", 20_000)
    _receivable(client, "comp-1790263124662", "store-ms-01", "2026-08", 7_000)
    d = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    ents = {e["id"]: e for e in d["entities"]}
    assert set(ents) == {"comp-1", "comp-1790263124662"}          # 只有帶分公司的總公司
    bh = ents["comp-1"]
    assert bh["revenue"] == 100_000
    assert bh["company_ids"] == ["comp-1", "comp-1790263075210", "comp-bh-hanshin"]
    assert next(c for c in d["companies"] if c["id"] == "comp-1")["revenue"] == 50_000   # 總公司本身不含分公司


def test_store_lines_count_under_store_company(client):
    """發票開總公司統編、但是巨蛋店的貨：單據屬總公司（同一法人），損益算巨蛋店所屬的高雄分公司。"""
    from tests.helpers import payable
    payable(client, company="comp-1", store="store-bhk-judan", period="2026-08", amount=10_500, invoice_no="QQ00000001")
    d = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    by = {c["id"]: c for c in d["companies"]}
    assert by["comp-1790263075210"]["stores_operating_profit"] == -10_000
    assert by["comp-1"]["stores_operating_profit"] == 0
