"""POS 營收與銀行入款重複：銀行入款先被建成應收（已收），改對到 POS 的應收；取消核對送回待確認。"""
from tests.test_bank_webhook import _send
from tests.test_pl import _receivable

ACC = "acc-006-tcb"   # 楊師傅


def _deposit(client, svc_headers, txn, amount, date="2026-09-03"):
    _send(client, svc_headers, {"cash_txn_id": txn, "cash_account_id": ACC, "txn_date": date, "direction": "in",
                                "amount": amount, "bank_memo": "特店入帳", "counterparty_name": "011860617436001"})
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    return next(i for i in items if i["payload"]["item"]["cash_txn_id"] == txn)


def _doc(client, doc_id):
    return client.get(f"/api/v1/documents/{doc_id}").json()


def test_bank_receivable_relinked_to_pos(client, svc_headers):
    pos1 = _receivable(client, "comp-2", "store-ys-01", "2026-09", 10_000, counterparty="信用卡")
    pos2 = _receivable(client, "comp-2", "store-ys-01", "2026-09", 12_000, counterparty="信用卡")
    rv = _deposit(client, svc_headers, "T-CARD-1", 21_500)
    # 會計先把入款建成一張「銀行」應收（造成營收重複）
    r = client.post(f"/api/v1/review-items/{rv['id']}/resolve", json={"action": "create_document",
                    "payload": {"category_code": "R05", "store_id": "store-ys-01"}})
    assert r.status_code == 200, r.text
    bank_doc = r.json()["document_id"]
    d = _doc(client, bank_doc)
    assert d["bank_txn_ids"] == ["T-CARD-1"] and d["settled_dates"] == ["2026-09-03"]

    r = client.post(f"/api/v1/documents/{bank_doc}/relink", json={
        "document_ids": [pos1["id"], pos2["id"]], "difference_mode": "book", "difference_category": "E11"})
    assert r.status_code == 200, r.text
    assert _doc(client, bank_doc)["status"] == "void"
    for p in (pos1, pos2):
        x = _doc(client, p["id"])
        assert x["settle_status"] == "settled" and x["settled_dates"] == ["2026-09-03"] and x["bank_txn_ids"] == ["T-CARD-1"]
    assert r.json()["difference"] == -500 and r.json()["difference_document"]
    # 待確認已處理，不會再出現
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    assert not [i for i in items if i["payload"]["item"]["cash_txn_id"] == "T-CARD-1"]


def test_batch_unmatch_sends_back_to_review(client, svc_headers):
    pos = _receivable(client, "comp-2", "store-ys-01", "2026-09", 8_000, counterparty="信用卡")
    rv = _deposit(client, svc_headers, "T-CARD-2", 7_900)
    r = client.post(f"/api/v1/review-items/{rv['id']}/resolve", json={"action": "manual_match", "payload": {
        "document_ids": [pos["id"]], "difference_mode": "book", "difference_category": "E11"}})
    assert r.status_code == 200, r.text
    assert _doc(client, pos["id"])["settle_status"] == "settled"

    r = client.post("/api/v1/documents/batch-unmatch", json={"ids": [pos["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["reopened"] == 1 and len(r.json()["results"][0]["voided_documents"]) == 1   # E11 差額單作廢
    assert _doc(client, pos["id"])["settle_status"] == "open"
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    assert [i for i in items if i["payload"]["item"]["cash_txn_id"] == "T-CARD-2"]
    # 沒有銀行紀錄的單據回錯誤、不影響其他
    other = _receivable(client, "comp-2", "store-ys-01", "2026-09", 100)
    r = client.post("/api/v1/documents/batch-unmatch", json={"ids": [other["id"]]})
    assert r.json()["reopened"] == 0 and r.json()["failed"]
