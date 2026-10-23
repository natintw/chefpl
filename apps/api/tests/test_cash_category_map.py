"""現金流分類 → 損益類別預判：待確認的預設類別與損益月份。"""
from tests.helpers import payable
from tests.test_bank_webhook import _send, _txn


def _sugg(client):
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    return {i["payload"]["item"]["cash_txn_id"]: i for i in items}


def test_cashflow_category_becomes_ledger_suggestion(client, svc_headers):
    _send(client, svc_headers,
          _txn("T-TEL", 1548, counterparty_name="某電信", cash_category_code="EXP_TEL", cash_category_name="電信費"),
          _txn("T-SAL", 30000, counterparty_name="馮耀文", cash_category_code="EXP_SAL", cash_category_name="薪資"),
          _txn("T-NOT", 15000, counterparty_name="某某", cash_category_code="EXP_NOT", cash_category_name="票據支付"))
    s = _sugg(client)
    assert (s["T-TEL"]["suggested_category_code"], s["T-TEL"]["suggested_period_mode"]) == ("E17", "same")
    assert "電信費" in s["T-TEL"]["suggestion_source"]
    assert (s["T-SAL"]["suggested_category_code"], s["T-SAL"]["suggested_period_mode"]) == ("P01", "prev")
    assert s["T-NOT"]["suggested_category_code"] is None


def test_new_cashflow_category_is_registered_and_mappable(client, svc_headers):
    _send(client, svc_headers, _txn("T-NEW", 2000, counterparty_name="某系統商",
                                    cash_category_code="EXP_CUSTOM1", cash_category_name="營業支出"))
    rows = {r["cash_code"]: r for r in client.get("/api/v1/settings/cash-category-map").json()["items"]}
    assert rows["EXP_CUSTOM1"]["cash_name"] == "營業支出" and rows["EXP_CUSTOM1"]["ledger_category_code"] is None
    assert rows["EXP_CUSTOM1"]["open_reviews"] == 1
    assert _sugg(client)["T-NEW"]["suggested_category_code"] is None
    r = client.patch("/api/v1/settings/cash-category-map/EXP_CUSTOM1", json={"ledger_category_code": "E16"})
    assert r.status_code == 200, r.text
    assert _sugg(client)["T-NEW"]["suggested_category_code"] == "E16"
    bad = client.patch("/api/v1/settings/cash-category-map/EXP_CUSTOM1", json={"ledger_category_code": "ZZZ"})
    assert bad.status_code == 422


def test_vendor_category_beats_cashflow_category(client, svc_headers):
    d = payable(client, company="comp-1", store="store-bhk-judan", amount=10500, vendor="協昌菓菜行", invoice_no="CM00000001")
    cp = client.get("/api/v1/settings/vendor-map", params={"q": "協昌"}).json()["items"][0]
    assert client.patch(f"/api/v1/settings/vendor-map/{cp['id']}", json={"category_code": "C01"}).status_code == 200
    _send(client, svc_headers, _txn("T-PUR", 9999, counterparty_name="協昌菓菜行",
                                    cash_category_code="EXP_PUR", cash_category_name="進貨付款"))
    g = _sugg(client)["T-PUR"]
    assert g["suggested_category_code"] == "C01" and "廠商類別" in g["suggestion_source"]
    assert d["id"]
