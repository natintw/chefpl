"""現金付款：選單據標成已付；一次付好幾張；取消；不同公司不能混在一起；不影響損益。"""
from tests.helpers import payable


def _pay(client, ids, **kw):
    return client.post("/api/v1/cash-payments", json={"document_ids": ids, "paid_date": "2026-09-30", **kw})


def test_cash_pays_several_documents_and_can_be_reversed(client):
    a = payable(client, company="comp-1", store="store-bh-judan", amount=10500, vendor="張鈞", invoice_no="CC00000001")
    b = payable(client, company="comp-1790263075210", store="store-bh-dale", amount=2100, vendor="張鈞", invoice_no="CC00000002")
    pl_before = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-1"}).json()
    r = _pay(client, [a["id"], b["id"]], source="store_cash", note="大樂店收銀付")
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["total"] == 12600 and res["payment_id"].startswith("cash-")
    for d in (a, b):
        doc = client.get(f"/api/v1/documents/{d['id']}").json()
        assert doc["settle_status"] == "settled" and doc["settlements"][0]["kind"] == "cash"
        assert doc["settlements"][0]["note"] == "大樂店收銀付"
    # 損益不變
    assert client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-1"}).json() == pl_before
    # 已付的不能再付
    assert _pay(client, [a["id"]]).status_code == 422
    # 取消：整筆一起回到未付
    r = client.post(f"/api/v1/cash-payments/{res['payment_id']}/reverse")
    assert r.status_code == 200, r.text
    for d in (a, b):
        assert client.get(f"/api/v1/documents/{d['id']}").json()["settle_status"] == "open"
    assert client.post(f"/api/v1/cash-payments/{res['payment_id']}/reverse").status_code == 404


def test_partial_cash_amount(client):
    a = payable(client, company="comp-1", store="store-bh-judan", amount=10500, vendor="柯文聖", invoice_no="CC00000003")
    r = _pay(client, [a["id"]], amount=10000)
    assert r.status_code == 201, r.text
    doc = client.get(f"/api/v1/documents/{a['id']}").json()
    assert doc["settle_status"] == "partial" and doc["outstanding"] == 500
    assert _pay(client, [a["id"]], amount=600).status_code == 422          # 超過未結清
    assert _pay(client, [a["id"]]).json()["total"] == 500                   # 不指定 → 付清剩下的


def test_cannot_mix_legal_entities(client):
    a = payable(client, company="comp-1", store="store-bh-judan", amount=1050, vendor="張鈞", invoice_no="CC00000004")
    b = payable(client, company="comp-2", store="store-ys-01", amount=1050, vendor="張鈞", invoice_no="CC00000005")
    r = _pay(client, [a["id"], b["id"]])
    assert r.status_code == 422 and "同一家公司" in r.text


def test_cash_paid_document_is_skipped_by_bank_matching(client, svc_headers):
    import uuid
    a = payable(client, company="comp-1", store="store-bh-judan", amount=10500, vendor="協昌菓菜行", invoice_no="CC00000006")
    _pay(client, [a["id"]])
    r = client.post("/integration/v1/webhooks/bank-transactions", headers={**svc_headers, "Idempotency-Key": str(uuid.uuid4())},
                    json={"items": [{"cash_txn_id": "B1", "cash_account_id": "acc-3926", "txn_date": "2026-09-30",
                                     "direction": "out", "amount": 10500, "counterparty_name": "協昌菓菜行"}]})
    assert r.json()["matched"] == 0   # 已用現金付掉，不會被銀行再付一次
