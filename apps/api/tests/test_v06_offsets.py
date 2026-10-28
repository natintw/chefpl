"""開發文件 v0.6：廠商主檔、預付款沖抵、折讓、代付（附錄 B 驗收範例）。"""
from tests.conftest import WH, WH_STORE
from tests.helpers import xlsx

from app.feature.vendors import valid_tax_id

CT = "comp-1790263102815"      # 鄧師傅滷味
CT_STORE = "store-ct-01"       # 中正店
BHK = "comp-1790263075210"     # 博竑高雄分公司（統編 93549405）
FUTONG_TAX = "23344294"
XIANGSHUN_TAX = "67816307"


def _vendor(client, name, tax_id=None, **kw):
    r = client.post("/api/v1/vendors", json={"name": name, "tax_id": tax_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _pl(client, company, period, store=None):
    params = {"period_from": period, "period_to": period, "company_id": company}
    if store:
        params["store_id"] = store
    st = client.get("/api/v1/pl/statement", params=params).json()
    return {l["category_code"]: l["values"]["total"] for s in st["sections"] for l in s["lines"]}


def _balance(client, company, code, as_of="2026-12-31"):
    b = client.get("/api/v1/balances", params={"as_of": as_of, "company_id": company}).json()
    for c in b["companies"]:
        if c["company_id"] == company:
            return next((x["amount"] for x in c["balances"] if x["category_code"] == code), 0)
    return 0


# ---------------------------------------------------------------- 廠商主檔
def test_tax_id_checksum():
    assert valid_tax_id(FUTONG_TAX) and valid_tax_id("42799631") and valid_tax_id("93549405")
    assert not valid_tax_id("57762085") and not valid_tax_id("5882653") and not valid_tax_id("010068779")


def test_vendor_requires_tax_id_or_flag(client):
    r = client.post("/api/v1/vendors", json={"name": "某某行"})
    assert r.status_code == 422 and r.json()["code"] == "TAX_ID_REQUIRED"
    r = client.post("/api/v1/vendors", json={"name": "某某行", "tax_id": "57762085"})
    assert r.status_code == 422                                     # 檢查碼不符
    v = _vendor(client, "雨來菇－張鈞", no_tax_id=True, id_masked="T12****011")
    assert v["no_tax_id"] and v["id_masked"] == "T1*****011"
    _vendor(client, "富統食品股份有限公司", FUTONG_TAX)
    r = client.post("/api/v1/vendors", json={"name": "富統", "tax_id": FUTONG_TAX})
    assert r.status_code == 409 and r.json()["code"] == "TAX_ID_EXISTS"


def test_vendor_search_by_tax_id_and_name(client):
    _vendor(client, "富統食品股份有限公司", FUTONG_TAX, aliases=["富統"])
    _vendor(client, "翔舜冷凍行", XIANGSHUN_TAX)
    assert client.get("/api/v1/vendors/search", params={"q": FUTONG_TAX}).json()["items"][0]["match"] == "tax_id"
    assert client.get("/api/v1/vendors/search", params={"q": "2334"}).json()["items"][0]["tax_id"] == FUTONG_TAX
    hits = client.get("/api/v1/vendors/search", params={"q": "富統食品"}).json()["items"]
    assert [h["name"] for h in hits] == ["富統食品股份有限公司"]


def test_vendor_import_preview_then_commit(client):
    # 主檔已有一家沒有統編的「協昌果菜行」（發票系統建立的）、一家有統編的富統
    client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-20", "counterparty_name": "協昌果菜行",
        "settle_amount": 1000, "lines": [{"period_key": "2026-08", "category_code": "C01", "amount": 1000,
                                          "store_id": WH_STORE}]})
    _vendor(client, "富統食品(股)公司", FUTONG_TAX)
    data = xlsx([["說明"], [],
                 ["廠商名稱", "廠商統編", "沒有統編", "遮蔽身分證", "簡稱", "預設類別", "付款關鍵字", "狀態"],
                 ["協昌果菜行", "13647452", "", "", "協昌", "C01", "協昌", "啟用"],
                 ["富統食品股份有限公司", FUTONG_TAX, "", "", "富統", "C02", "", "啟用"],
                 ["鮮果多股份有限公司", "85008926", "", "", "", "", "", "啟用"],
                 ["眾吉果菜行", "85478200", "", "", "", "", "", "停用"],
                 ["吳郭魚", "", "Y", "S12****729", "", "", "", "啟用"],
                 ["南誠實業股份有限公司高雄修護廠", "57762085", "", "", "", "", "", "啟用"]])
    files = {"file": ("v.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    pre = client.post("/api/v1/vendors/import", files=files, data={"commit": "false"}).json()
    assert pre["committed"] is False
    acts = {r["name"]: r["action"] for r in pre["rows"]}
    assert acts == {"協昌果菜行": "fill_tax_id", "富統食品股份有限公司": "name_differs",
                    "鮮果多股份有限公司": "create", "眾吉果菜行": "create", "吳郭魚": "create",
                    "南誠實業股份有限公司高雄修護廠": "error"}
    assert client.get("/api/v1/vendors/search", params={"q": "13647452"}).json()["items"] == []  # 預覽不寫入

    row = next(r["row"] for r in pre["rows"] if r["action"] == "name_differs")
    files = {"file": ("v.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    done = client.post("/api/v1/vendors/import", files=files,
                       data={"commit": "true", "decisions": f'{{"{row}": "update_name"}}'}).json()
    assert done["committed"] and done["counts"]["create"] == 3
    xc = client.get("/api/v1/vendors/search", params={"q": "13647452"}).json()["items"][0]
    assert xc["name"] == "協昌果菜行" and xc["default_category_code"] == "C01"
    ft = client.get("/api/v1/vendors/search", params={"q": FUTONG_TAX}).json()["items"][0]
    assert ft["name"] == "富統食品股份有限公司" and "富統食品(股)公司" in ft["aliases"]
    assert client.get("/api/v1/vendors/search", params={"q": "85478200"}).json()["items"] == []  # 停用不顯示
    assert client.get("/api/v1/vendors", params={"missing_tax_id": True}).json()["missing_tax_id_count"] == 0


# ---------------------------------------------------------------- 附錄 B.1：富統（訂金 → 發票 → 折讓 → 尾款）
def test_futong_prepay_invoice_allowance(client):
    ft = _vendor(client, "富統食品股份有限公司", FUTONG_TAX, default_category_code="C02")
    pre = client.post("/api/v1/prepayments", json={
        "company_id": WH, "store_id": WH_STORE, "counterparty_id": ft["id"], "doc_date": "2026-10-08",
        "amount": 30000, "order_total": 105000, "expected_invoice_period": "2026-10",
        "planned_category_code": "C02", "memo": "年菜豬腳、牛腱訂金"})
    assert pre.status_code == 201, pre.text
    pre = pre.json()
    assert pre["doc_type"] == "prepayment" and pre["outstanding"] == 30000     # 等銀行付款
    assert _pl(client, WH, "2026-10", WH_STORE).get("C02", 0) == 0             # 訂金不進損益
    assert _balance(client, WH, "B07") == 30000

    inv = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-10-28", "counterparty_id": ft["id"],
        "invoice_no": "AB12345678", "has_invoice": True, "settle_amount": 105000, "tax_amount": 5000,
        "lines": [{"period_key": "2026-10", "category_code": "C02", "amount": 100000, "store_id": WH_STORE}]}).json()
    items = client.get("/api/v1/review-items", params={"kind": "prepayment_match"}).json()["items"]
    assert len(items) == 1 and items[0]["payload"]["candidates"][0]["remaining"] == 30000
    r = client.post(f"/api/v1/review-items/{items[0]['id']}/resolve", json={"action": "apply_offset"})
    assert r.status_code == 200, r.text
    inv = client.get(f"/api/v1/documents/{inv['id']}").json()
    assert inv["outstanding"] == 75000 and inv["offset_amount"] == 30000 and inv["settle_status"] == "partial"
    assert _balance(client, WH, "B07") == 0

    # 原單已沖完訂金，再沖會超過預付剩餘
    r = client.post("/api/v1/offsets", json={"kind": "prepayment", "from_document_id": pre["id"],
                                              "to_document_id": inv["id"], "amount": 1})
    assert r.status_code == 422 and r.json()["code"] == "EXCEEDS_PREPAYMENT"

    found = client.get("/api/v1/documents/lookup", params={"invoice_no": "AB12345678",
                                                           "seller_tax_id": FUTONG_TAX}).json()["items"]
    assert [d["id"] for d in found] == [inv["id"]]
    al = client.post("/api/v1/allowances", json={
        "original_document_id": inv["id"], "doc_date": "2026-11-03", "reason": "quality", "untaxed": 5000,
        "tax": 250, "refund_method": "reduce_outstanding", "allowance_no": "ZR-20261103-01",
        "memo": "牛腱品質不符"})
    assert al.status_code == 201, al.text
    al = al.json()
    assert al["lines"][0]["period_key"] == "2026-10" and al["lines"][0]["amount"] == -5000
    assert al["tax_amount"] == -250 and al["settle_status"] == "settled"
    inv = client.get(f"/api/v1/documents/{inv['id']}").json()
    assert inv["outstanding"] == 69750
    assert _pl(client, WH, "2026-10", WH_STORE)["C02"] == 95000

    dup = client.post("/api/v1/allowances", json={
        "original_document_id": inv["id"], "doc_date": "2026-11-04", "reason": "quality", "untaxed": 100,
        "tax": 5, "refund_method": "reduce_outstanding", "allowance_no": "ZR-20261103-01"})
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_ALLOWANCE"

    chain = client.get(f"/api/v1/documents/{inv['id']}/chain").json()
    assert [d["doc_type"] for d in chain["documents"]] == ["prepayment", "payable", "journal", "allowance"]

    # 有沖抵紀錄的應付不能直接作廢
    r = client.post(f"/api/v1/documents/{inv['id']}/void", json={"reason": "測試"})
    assert r.status_code == 409 and r.json()["code"] == "HAS_OFFSETS"


def test_prepayment_requires_vendor_tax_id(client):
    r = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-10-01", "counterparty_name": "無名小販",
        "settle_amount": 100, "lines": [{"period_key": "2026-10", "category_code": "C01", "amount": 100}]}).json()
    cp_id = r["counterparty"]["id"]
    r = client.post("/api/v1/prepayments", json={"company_id": WH, "counterparty_id": cp_id,
                                                  "doc_date": "2026-10-08", "amount": 1000})
    assert r.status_code == 422 and r.json()["code"] == "TAX_ID_REQUIRED"


def test_offset_reverse_restores_outstanding(client):
    ft = _vendor(client, "富統食品股份有限公司", FUTONG_TAX)
    pre = client.post("/api/v1/prepayments", json={"company_id": WH, "counterparty_id": ft["id"],
                                                    "doc_date": "2026-10-08", "amount": 30000}).json()
    inv = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-10-28", "counterparty_id": ft["id"],
        "has_invoice": True, "settle_amount": 105000, "tax_amount": 5000,
        "lines": [{"period_key": "2026-10", "category_code": "C02", "amount": 100000}]}).json()
    off = client.post("/api/v1/offsets", json={"kind": "prepayment", "from_document_id": pre["id"],
                                                "to_document_id": inv["id"], "amount": 20000}).json()
    assert client.get(f"/api/v1/documents/{inv['id']}").json()["outstanding"] == 85000
    assert _balance(client, WH, "B07") == 10000                          # 分次沖：還剩 10,000
    r = client.post(f"/api/v1/offsets/{off['id']}/reverse", json={"reason": "沖錯"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/documents/{inv['id']}").json()["outstanding"] == 105000
    assert _balance(client, WH, "B07") == 30000


def test_allowance_on_paid_invoice_becomes_vendor_credit(client):
    ft = _vendor(client, "富統食品股份有限公司", FUTONG_TAX)
    inv = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-10-28", "counterparty_id": ft["id"],
        "has_invoice": True, "settle_amount": 10500, "tax_amount": 500, "settlement_method": "petty_cash",
        "lines": [{"period_key": "2026-10", "category_code": "C02", "amount": 10000}]}).json()
    r = client.post("/api/v1/allowances", json={
        "original_document_id": inv["id"], "doc_date": "2026-11-03", "reason": "return", "untaxed": 1000,
        "tax": 50, "refund_method": "reduce_outstanding"})
    assert r.status_code == 422 and r.json()["code"] == "EXCEEDS_OUTSTANDING"
    al = client.post("/api/v1/allowances", json={
        "original_document_id": inv["id"], "doc_date": "2026-11-03", "reason": "return", "untaxed": 1000,
        "tax": 50, "refund_method": "credit_next"}).json()
    assert al["settle_status"] == "open" and al["outstanding"] == 1050       # 廠商欠我們
    nxt = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-11-20", "counterparty_id": ft["id"],
        "has_invoice": True, "settle_amount": 21000, "tax_amount": 1000,
        "lines": [{"period_key": "2026-11", "category_code": "C02", "amount": 20000}]}).json()
    r = client.post("/api/v1/offsets", json={"kind": "vendor_credit", "from_document_id": al["id"],
                                              "to_document_id": nxt["id"], "amount": 1050})
    assert r.status_code == 201, r.text
    assert client.get(f"/api/v1/documents/{nxt['id']}").json()["outstanding"] == 19950
    assert client.get(f"/api/v1/documents/{al['id']}").json()["settle_status"] == "settled"


# ---------------------------------------------------------------- 附錄 B.2：博竑高雄代付中正店修繕
def test_on_behalf_intercompany(client):
    xs = _vendor(client, "翔舜冷凍行", XIANGSHUN_TAX)
    r = client.post("/api/v1/on-behalf", json={
        "beneficiary_company_id": CT, "store_id": CT_STORE, "vendor_id": xs["id"], "category_code": "E01",
        "payer_company_id": BHK, "doc_date": "2026-10-15", "untaxed": 12000, "tax": 600,
        "invoice_no": "CD55667788", "invoice_buyer_tax_id": "93549405", "memo": "冷凍設備維修"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["header_is_payer"] is True and res["warnings"] == []
    d1 = client.get(f"/api/v1/documents/{res['beneficiary_document_id']}").json()
    d2 = client.get(f"/api/v1/documents/{res['payer_document_id']}").json()
    assert d1["settle_status"] == "settled" and d1["settled_by"] == "paid_by_other" and d1["outstanding"] == 0
    assert d2["outstanding"] == 12600 and d2["on_behalf_of_company_id"] == CT
    assert sum(1 for l in d2["lines"] if l["account_override_id"]) == 2   # 代付（進項）＋代支出（銷項）

    assert _pl(client, CT, "2026-10", CT_STORE)["E01"] == 12000
    assert "E01" not in _pl(client, BHK, "2026-10")                       # 付款公司損益不變
    assert _balance(client, CT, "B05") == -12600                          # 中正店欠博竑高雄
    assert _balance(client, BHK, "B05") == 12600                          # 博竑高雄：滷味欠我們

    # 付款方的代付單出現在未付清單，受益方那張不會
    open_ids = {d["id"] for d in client.get("/api/v1/documents", params={"doc_type": "payable",
                                                                          "settle_status": "open"}).json()["items"]}
    assert d2["id"] in open_ids and d1["id"] not in open_ids

    r = client.post(f"/api/v1/on-behalf/{d1['id']}/void", json={"reason": "建錯"})
    assert r.status_code == 200
    assert _balance(client, CT, "B05") == 0 and _balance(client, BHK, "B05") == 0


def test_on_behalf_same_legal_entity_rejected(client):
    xs = _vendor(client, "翔舜冷凍行", XIANGSHUN_TAX)
    r = client.post("/api/v1/on-behalf", json={
        "beneficiary_company_id": BHK, "vendor_id": xs["id"], "category_code": "E01",
        "payer_company_id": "comp-1", "doc_date": "2026-10-15", "untaxed": 1000, "tax": 50})
    assert r.status_code == 422 and r.json()["code"] == "SAME_COMPANY"
