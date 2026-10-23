"""銀行交易 webhook：一家公司一次匯一筆 → 拆回各門市的應付；記住對方帳號；認不出時進待確認並附建議。"""
import uuid

from app.feature.bank_matching import core_name
from tests.helpers import payable

HOOK = "/integration/v1/webhooks/bank-transactions"


def _send(client, svc_headers, *items):
    r = client.post(HOOK, json={"items": list(items)}, headers={**svc_headers, "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()


def _txn(txn_id, amount, **kw):
    return {"cash_txn_id": txn_id, "cash_account_id": "acc-3926", "txn_date": "2026-09-30", "direction": "out",
            "amount": amount, "bank_memo": "跨行轉帳", **kw}


def _vendor_payables(client):
    # 協昌：博竑總公司、高雄分公司、漢神分公司各一張（同一法人），另一家廠商一張
    a = payable(client, company="comp-1", store="store-bh-judan", amount=10500, vendor="協昌菓菜行", invoice_no="AB00000001")
    b = payable(client, company="comp-1790263075210", store="store-bh-dale", amount=21000, vendor="協昌菓菜行",
                invoice_no="AB00000002")
    c = payable(client, company="comp-bh-hanshin", store="store-bh-counter", amount=5250, vendor="協昌菓菜行",
                invoice_no="AB00000003")
    o = payable(client, company="comp-1", store="store-bh-judan", amount=8400, vendor="美食家食材通路股份有限公司",
                invoice_no="AB00000004")
    return a, b, c, o


def test_core_name():
    assert core_name("美食家食材通路股份有限公司") == "美食家食材通路"
    assert core_name("明台產物保險股份有限公司高雄分公司") == "明台產物保險"
    assert core_name("協昌菓菜行") == "協昌菓菜"


def test_one_transfer_settles_all_stores(client, svc_headers):
    a, b, c, o = _vendor_payables(client)
    res = _send(client, svc_headers, _txn("T1", 36750, counterparty_name="協昌菓菜", counterparty_account_no="****12345"))
    assert res["matched"] == 1 and set(res["results"][0]["documents"]) == {a["id"], b["id"], c["id"]}
    for d in (a, b, c):
        assert client.get(f"/api/v1/documents/{d['id']}").json()["settle_status"] == "settled"
    assert client.get(f"/api/v1/documents/{o['id']}").json()["settle_status"] == "open"
    # 同一筆重送：略過
    assert _send(client, svc_headers, _txn("T1", 36750, counterparty_name="協昌菓菜"))["duplicate"] == 1


def test_account_is_remembered(client, svc_headers):
    _vendor_payables(client)
    _send(client, svc_headers, _txn("T1", 10500, counterparty_name="協昌菓菜行", counterparty_account_no="0123-12345"))
    # 第二筆沒有戶名、摘要也看不出來，只有帳號 → 靠記住的帳號認出協昌
    res = _send(client, svc_headers, _txn("T2", 26250, bank_memo="ATM轉帳", counterparty_account_no="***12345"))
    assert res["matched"] == 1 and res["results"][0]["matched_by"] == "account"


def test_unknown_payee_goes_to_review_with_suggestion(client, svc_headers):
    a, b, c, o = _vendor_payables(client)
    res = _send(client, svc_headers, _txn("T9", 8400, bank_memo="票交媒體"))
    assert res["review"] == 1
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    sug = items[0]["payload"]["suggestion"]
    assert sug["counterparty_name"] == "美食家食材通路股份有限公司" and [d["document_id"] for d in sug["documents"]] == [o["id"]]
    r = client.post(f"/api/v1/review-items/{items[0]['id']}/resolve", json={"action": "settle_suggestion", "payload": {}})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/documents/{o['id']}").json()["settle_status"] == "settled"


def test_amount_not_adding_up_goes_to_review(client, svc_headers):
    _vendor_payables(client)
    res = _send(client, svc_headers, _txn("T3", 99999, counterparty_name="協昌菓菜行"))
    assert res["review"] == 1 and "湊不出" in res["results"][0]["reason"]


def test_incoming_matches_unique_receivable(client, svc_headers):
    r = client.post("/api/v1/documents", json={
        "doc_type": "receivable", "company_id": "comp-1", "doc_date": "2026-09-30", "counterparty_name": "家福",
        "settle_amount": 52500, "tax_amount": 2500,
        "lines": [{"period_key": "2026-09", "category_code": "R04", "amount": 50000, "store_id": "store-bh-factory"}]})
    assert r.status_code == 201, r.text
    res = _send(client, svc_headers, _txn("IN1", 52500, direction="in", bank_memo="家福匯款"))
    assert res["matched"] == 1
    assert client.get(f"/api/v1/documents/{r.json()['id']}").json()["settle_status"] == "settled"


def test_public_mode_requires_valid_signature(client, svc_headers, monkeypatch):
    """不同 Railway project：走公開網址，必須帶 HMAC 簽章（X-Timestamp、X-Signature）。"""
    import hashlib
    import hmac
    import json
    import time

    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_mode", "public")
    monkeypatch.setattr(settings, "integration_signing_secret", "s3cret")
    _vendor_payables(client)
    body = json.dumps({"items": [_txn("TS1", 10500, counterparty_name="協昌菓菜行")]}).encode()

    def sign(ts, b):
        msg = f"{ts}\nPOST\n{HOOK}\n{hashlib.sha256(b).hexdigest()}"
        return "v1=" + hmac.new(b"s3cret", msg.encode(), hashlib.sha256).hexdigest()

    base = {**svc_headers, "Content-Type": "application/json", "Idempotency-Key": "k-" + str(uuid.uuid4())}
    assert client.post(HOOK, content=body, headers=base).status_code == 401                       # 沒簽章
    ts = str(int(time.time()))
    bad = {**base, "X-Timestamp": ts, "X-Signature": sign(ts, body + b" ")}
    assert client.post(HOOK, content=body, headers=bad).status_code == 401                        # 內容被改
    old = str(int(time.time()) - 600)
    assert client.post(HOOK, content=body, headers={**base, "X-Timestamp": old,
                                                    "X-Signature": sign(old, body)}).status_code == 401  # 超過 5 分鐘
    ok = client.post(HOOK, content=body, headers={**base, "X-Timestamp": ts, "X-Signature": sign(ts, body)})
    assert ok.status_code == 200 and ok.json()["matched"] == 1
