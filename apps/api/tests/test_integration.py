"""現金流系統對接：金鑰、open-items、settlements（冪等）、bank-only-items → 待確認 → 建單、balances。"""
import uuid

from tests.conftest import ACC_3578, WH
from tests.helpers import payable


def _settle(client, h, doc_id, amount, level="exact", txn="txn-1", key=None, action="apply", bank_amount=None,
            account=ACC_3578):
    return client.post("/integration/v1/settlements", headers={**h, "Idempotency-Key": key or str(uuid.uuid4())},
                       json={"items": [{"document_id": doc_id, "cash_txn_id": txn, "cash_account_id": account,
                                        "txn_date": "2026-09-28", "amount": amount, "match_level": level,
                                        "action": action, "bank_amount": bank_amount}]})


def test_requires_key(client):
    assert client.get("/integration/v1/open-items").status_code == 401
    assert client.get("/integration/v1/open-items", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_open_items_and_settlement_flow(client, svc_headers):
    d = payable(client, amount=41850)
    items = client.get("/integration/v1/open-items", headers=svc_headers).json()
    assert [i["document_id"] for i in items["items"]] == [d["id"]]
    assert items["items"][0]["payment_window"] == {"from": "2026-09-25", "to": "2026-10-10"}

    r = _settle(client, svc_headers, d["id"], 41850, key="k-0001-aaaa")
    assert r.json()["applied"] == 1 and r.json()["documents"][0]["settle_status"] == "settled"

    # 同一把 key 重送：回傳第一次的結果，不重複套用
    again = _settle(client, svc_headers, d["id"], 41850, key="k-0001-aaaa")
    assert again.headers.get("Idempotent-Replayed") == "true" and again.json()["applied"] == 1
    # 同 key 不同內容：409
    assert _settle(client, svc_headers, d["id"], 100, key="k-0001-aaaa").status_code == 409
    # 新 key、同一筆交易：算重複，不重複入帳
    assert _settle(client, svc_headers, d["id"], 41850).json()["duplicates"] == 1

    after = client.get("/integration/v1/open-items", headers=svc_headers,
                       params={"updated_after": items["as_of"]}).json()
    assert after["items"] == [] and after["removed"] == [d["id"]]

    # 取消配對 → 回到未付
    rev = _settle(client, svc_headers, d["id"], 41850, action="reverse")
    assert rev.json()["documents"][0]["settle_status"] == "open"


def test_fee_diff_partial_amount_only_and_rejections(client, svc_headers):
    d = payable(client, amount=21300, vendor="鮮果多")
    r = _settle(client, svc_headers, d["id"], 21300, level="fee_diff", bank_amount=21285, txn="t-fee")
    assert r.json()["rejected"][0]["code"] == "TXN_OVER_ALLOCATED"   # 分配 21300 > 銀行 21285
    r = _settle(client, svc_headers, d["id"], 21300, level="fee_diff", txn="t-fee2")
    assert r.json()["documents"][0]["settle_status"] == "settled"

    d2 = payable(client, amount=10000, vendor="勝美行", has_invoice=False, category="C02")
    assert _settle(client, svc_headers, d2["id"], 4000, level="partial", txn="t-p").json()["documents"][0][
        "settle_status"] == "partial"
    q = _settle(client, svc_headers, d2["id"], 6000, level="amount_only", txn="t-q").json()
    assert q["queued_for_review"] == 1
    kinds = [i["kind"] for i in client.get("/api/v1/review-items").json()["items"]]
    assert "amount_only_match" in kinds

    wrong = _settle(client, svc_headers, d2["id"], 100, txn="t-x", account="acc-8249").json()   # 楊師傅的帳戶
    assert wrong["rejected"][0]["code"] == "COMPANY_MISMATCH"
    missing = _settle(client, svc_headers, str(uuid.uuid4()), 100, txn="t-y").json()
    assert missing["rejected"][0]["code"] == "DOCUMENT_NOT_FOUND"


def test_branch_paid_from_hq_account_is_allowed(client, svc_headers):
    # 文衡分公司的應付，由名勝食品（總公司）3578 帳戶付款：同一法人，允許
    d = payable(client, company=WH, amount=1050)
    assert _settle(client, svc_headers, d["id"], 1050).json()["applied"] == 1


def test_bank_only_to_document_and_balances(client, svc_headers):
    key = {"Idempotency-Key": "bank-only-0001"}
    body = {"items": [
        {"cash_txn_id": "t-int", "cash_account_id": "acc-1790321611123", "company_id": "comp-1790263102815",
         "txn_date": "2026-08-31", "direction": "out", "amount": 12480, "bank_memo": "放款利息",
         "suggested_kind": "interest", "suggested_category_code": "N01"},
        {"cash_txn_id": "t-prin", "cash_account_id": "acc-1790321611123", "company_id": "comp-1790263102815",
         "txn_date": "2026-08-31", "direction": "out", "amount": 50000, "bank_memo": "攤還本金",
         "suggested_kind": "loan_principal", "suggested_category_code": "B02"},
        {"cash_txn_id": "t-draw", "cash_account_id": "acc-1790321611123", "company_id": "comp-1790263102815",
         "txn_date": "2026-08-01", "direction": "in", "amount": 1000000, "bank_memo": "撥貸",
         "suggested_kind": "loan_drawdown", "suggested_category_code": "B02"}]}
    r = client.post("/integration/v1/bank-only-items", headers={**svc_headers, **key}, json=body)
    assert r.json() == {"received": 3, "duplicates": 0, "rejected": []}
    again = client.post("/integration/v1/bank-only-items", headers={**svc_headers, "Idempotency-Key": "other-key-01"},
                        json=body)
    assert again.json()["duplicates"] == 3

    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    for it in items:
        code = it["payload"]["item"]["suggested_category_code"]
        res = client.post(f"/api/v1/review-items/{it['id']}/resolve",
                          json={"action": "create_document", "payload": {"category_code": code,
                                                                         "counterparty_name": "臺灣銀行"}})
        assert res.status_code == 200, res.text

    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08",
                                                     "company_id": "comp-1790263102815"}).json()
    nonop = next(s for s in st["sections"] if s["section"] == "non_operating")["subtotal"]["total"]
    assert nonop == -12480                                    # 只有利息進損益
    bal = client.get("/integration/v1/balances", headers=svc_headers, params={"as_of": "2026-08-31"}).json()
    b02 = next(b for c in bal["companies"] for b in c["balances"] if b["category_code"] == "B02")
    assert b02["amount"] == -950000                           # 尚欠本金 95 萬
    assert b02["by_counterparty"][0]["counterparty_name"] == "臺灣銀行"
    # 已建單的交易，不會再出現在 open-items
    assert client.get("/integration/v1/open-items", headers=svc_headers).json()["items"] == []
