"""待確認的銀行交易手動核對：金額有些許差距、對方帳號更正。"""
import uuid

from tests.helpers import payable
from tests.test_bank_webhook import HOOK, _send, _txn


def _review_for(client, txn):
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    return next(i for i in items if i["payload"]["item"]["cash_txn_id"] == txn)


def _match(client, txn, ids, **kw):
    it = _review_for(client, txn)
    return client.post(f"/api/v1/review-items/{it['id']}/resolve",
                       json={"action": "manual_match", "payload": {"document_ids": ids, **kw}})


def _doc(client, d):
    return client.get(f"/api/v1/documents/{d['id']}").json()


def _pl_total(client, code, company="comp-1"):
    d = client.get("/api/v1/pl/statement", params={"period_from": "2026-09", "period_to": "2026-09",
                                                   "company_id": company, "columns": "month"}).json()
    return d


def test_overpaid_with_bank_fee(client, svc_headers):
    """多付 30（匯費內含）：單據結清，30 記銀行手續費。"""
    a = payable(client, company="comp-1", store="store-bh-factory", amount=10500, vendor="元華行", invoice_no="MM00000001")
    _send(client, svc_headers, _txn("M1", 10530, counterparty_name="某某", txn_date="2026-09-30"))
    r = _match(client, "M1", [a["id"]], difference_mode="book")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["difference"] == 30 and res["difference_document"]
    assert _doc(client, a)["settle_status"] == "settled"
    fee = client.get(f"/api/v1/documents/{res['difference_document']}").json()
    assert fee["lines"][0]["category_code"] == "E12" and fee["settle_status"] == "settled"


def test_underpaid_left_open_then_written_off(client, svc_headers):
    a = payable(client, company="comp-1", store="store-bh-factory", amount=10500, vendor="元華行", invoice_no="MM00000002")
    b = payable(client, company="comp-1790263075210", store="store-bhk-dale", amount=5250, vendor="元華行", invoice_no="MM00000003")
    # 少付 750，留著未付
    _send(client, svc_headers, _txn("M2", 15000, counterparty_name="某某", txn_date="2026-09-30"))
    r = _match(client, "M2", [a["id"], b["id"]], difference_mode="leave_open")
    assert r.status_code == 200, r.text
    assert _doc(client, a)["settle_status"] == "settled"
    assert _doc(client, b)["settle_status"] == "partial" and _doc(client, b)["outstanding"] == 750


def test_underpaid_written_off_as_discount(client, svc_headers):
    a = payable(client, company="comp-1", store="store-bh-factory", amount=10500, vendor="元華行", invoice_no="MM00000004")
    b = payable(client, company="comp-1", store="store-bh-factory", amount=5250, vendor="元華行", invoice_no="MM00000005")
    _send(client, svc_headers, _txn("M3", 15700, counterparty_name="某某", txn_date="2026-09-30"))  # 抹零 50
    r = _match(client, "M3", [a["id"], b["id"]], difference_mode="book")
    assert r.status_code == 200, r.text
    assert _doc(client, a)["settle_status"] == "settled" and _doc(client, b)["settle_status"] == "settled"
    adj = client.get(f"/api/v1/documents/{r.json()['difference_document']}").json()
    assert adj["lines"][0]["category_code"] == "N03" and adj["settle_amount"] == 50
    # 銀行那筆只分出 15,700
    st = [s for s in _doc(client, a)["settlements"] + _doc(client, b)["settlements"] if s["cash_txn_id"] == "M3"]
    assert sum(s["amount"] for s in st) == 15700
    # 現金流刪掉這筆：兩張單據回未付，差額單據作廢
    rr = client.post(HOOK, json={"event": "bank_transactions.deleted", "items": [{"cash_txn_id": "M3"}]},
                     headers={**svc_headers, "Idempotency-Key": str(uuid.uuid4())})
    assert rr.status_code == 200, rr.text
    assert _doc(client, a)["settle_status"] == "open" and _doc(client, b)["settle_status"] == "open"
    assert client.get(f"/api/v1/documents/{r.json()['difference_document']}").json()["status"] == "void"


def test_receipt_short_by_card_fee(client, svc_headers):
    r = client.post("/api/v1/documents", json={
        "doc_type": "receivable", "company_id": "comp-1", "doc_date": "2026-09-30", "counterparty_name": "家福",
        "settle_amount": 52500, "tax_amount": 2500,
        "lines": [{"period_key": "2026-09", "category_code": "R04", "amount": 50000, "store_id": "store-bh-factory"}]})
    _send(client, svc_headers, _txn("M4", 51975, direction="in", txn_date="2026-10-02"))   # 被扣 525
    res = _match(client, "M4", [r.json()["id"]], difference_mode="book")
    assert res.status_code == 200, res.text
    assert client.get(f"/api/v1/documents/{r.json()['id']}").json()["settle_status"] == "settled"
    fee = client.get(f"/api/v1/documents/{res.json()['difference_document']}").json()
    assert fee["doc_type"] == "payable" and fee["lines"][0]["category_code"] == "E11"


def test_wrong_account_is_corrected(client, svc_headers):
    """對方帳號原本被記在錯的廠商身上：手動核對後改記到正確廠商，下次自動對上。"""
    a = payable(client, company="comp-1", store="store-bh-factory", amount=10500, vendor="元華行", invoice_no="MM00000006")
    wrong = payable(client, company="comp-1", store="store-bh-factory", amount=3150, vendor="協昌菓菜行", invoice_no="MM00000007")
    _send(client, svc_headers, _txn("W0", 3150, counterparty_name="協昌菓菜行", counterparty_account_no="0123-77777",
                                    txn_date="2026-09-30"))  # 系統記住：77777＝協昌（其實記錯）
    _send(client, svc_headers, _txn("W1", 10500, counterparty_account_no="0123-77777", bank_memo="ATM轉帳",
                                    txn_date="2026-09-30"))  # 用帳號認成協昌，但協昌已付清 → 待確認
    r = _match(client, "W1", [a["id"]])
    assert r.status_code == 200 and "元華行" in r.json()["remembered_account"]
    b = payable(client, company="comp-1", store="store-bh-factory", amount=2100, vendor="元華行", invoice_no="MM00000008")
    res = _send(client, svc_headers, _txn("W2", 2100, counterparty_account_no="***77777", bank_memo="ATM轉帳",
                                          txn_date="2026-09-30"))
    assert res["matched"] == 1 and res["results"][0]["counterparty"].startswith("元華")


def test_overpaid_requires_category_mode(client, svc_headers):
    a = payable(client, company="comp-1", store="store-bh-factory", amount=1050, vendor="元華行", invoice_no="MM00000009")
    _send(client, svc_headers, _txn("M5", 2000, counterparty_name="某某", txn_date="2026-09-30"))
    assert _match(client, "M5", [a["id"]], difference_mode="leave_open").status_code == 422


def test_batch_salary_to_previous_month(client, svc_headers):
    """9 月匯出的薪資（8 月的薪資）：批次建立 P01，損益記在 8 月、公司層級。"""
    sal = {"cash_category_code": "EXP_SAL", "cash_category_name": "薪資"}
    _send(client, svc_headers, _txn("SAL1", 32000, counterparty_name="王小明", txn_date="2026-09-05", **sal),
          _txn("SAL2", 28000, counterparty_name="李小華", txn_date="2026-09-05", **sal))
    items = [i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
             if i["payload"]["item"]["cash_txn_id"] in ("SAL1", "SAL2")]
    assert len(items) == 2 and items[0]["payload"]["item"]["cash_category_code"] == "EXP_SAL"
    r = client.post("/api/v1/review-items/batch", json={"ids": [i["id"] for i in items], "action": "create_document",
                                                       "payload": {"category_code": "P01", "period_mode": "prev"}})
    assert r.status_code == 200 and len(r.json()["ok"]) == 2, r.text
    pl = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-1",
                                                    "columns": "month"}).json()
    assert "60,000" in str(pl).replace("60000", "60,000")
    assert not [i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
                if i["payload"]["item"]["cash_txn_id"] in ("SAL1", "SAL2")]


def test_quiet_category_not_reviewed_and_cleanup(client, svc_headers):
    """現金存提、內部調度：對不到不進待確認；先前沒帶分類而進了待確認的，重送帶分類後自動撤下。"""
    res = _send(client, svc_headers, _txn("Q1", 5000, txn_date="2026-09-05", cash_category_code="CSH_WDL",
                                          cash_category_name="現金提領"))
    assert res["skipped"] == 1
    _send(client, svc_headers, _txn("Q2", 7000, txn_date="2026-09-05"))           # 沒帶分類 → 待確認
    res = _send(client, svc_headers, _txn("Q2", 7000, txn_date="2026-09-05", cash_category_code="INT_TRN",
                                          cash_category_name="內部調度"))
    assert res["skipped"] == 1
    st = client.post(HOOK, json={"event": "bank_transactions.status", "items": [{"cash_txn_id": "Q1"}, {"cash_txn_id": "Q2"}]},
                     headers={**svc_headers, "Idempotency-Key": str(uuid.uuid4())}).json()
    assert {r["status"] for r in st["results"]} == {"skipped"}
    # 重送沒帶分類的交易：分類補上
    _send(client, svc_headers, _txn("Q3", 1234, txn_date="2026-09-05"))
    _send(client, svc_headers, _txn("Q3", 1234, txn_date="2026-09-05", cash_category_code="EXP_SAL", cash_category_name="薪資"))
    it = next(i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
              if i["payload"]["item"]["cash_txn_id"] == "Q3")
    assert it["payload"]["item"]["cash_category_name"] == "薪資"


def test_salary_by_store_is_remembered(client, svc_headers):
    """薪資放到門市：批次時逐筆指定門市；下次同一個人自動建議那家門市；不會開「新對象」待確認。"""
    sal = {"cash_category_code": "EXP_SAL", "cash_category_name": "薪資", "cash_account_id": "acc-3312"}
    _send(client, svc_headers, _txn("ST1", 30000, counterparty_name="王小明", txn_date="2026-09-05", **sal),
          _txn("ST2", 28000, counterparty_name="李小華", txn_date="2026-09-05", **sal))
    items = {i["payload"]["item"]["cash_txn_id"]: i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]}
    r = client.post("/api/v1/review-items/batch", json={
        "ids": [items["ST1"]["id"], items["ST2"]["id"]], "action": "create_document",
        "payload": {"category_code": "P01", "period_mode": "prev", "store_by_item": {items["ST1"]["id"]: "store-ys-01", items["ST2"]["id"]: ""}}})
    assert r.status_code == 200 and len(r.json()["ok"]) == 2, r.text
    kinds = [i["kind"] for i in client.get("/api/v1/review-items").json()["items"]]
    assert "new_counterparty" not in kinds
    pl = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-2",
                                                    "store_id": "store-ys-01"}).json()
    assert "30000" in str(pl)
    _send(client, svc_headers, _txn("ST3", 30500, counterparty_name="王小明", txn_date="2026-10-05", **sal))
    nxt = next(i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
               if i["payload"]["item"]["cash_txn_id"] == "ST3")
    assert nxt["suggested_store_id"] == "store-ys-01"


def test_batch_fixed_month_company_level(client, svc_headers):
    """指定月份 2026-08、公司層級：楊師傅 8 月損益（公司合計）出現薪資。"""
    _send(client, svc_headers, _txn("FX1", 14541, counterparty_name="施駿緯", bank_memo="網路轉帳", bank_note="施駿緯８月薪資",
                                    txn_date="2026-09-07", cash_account_id="acc-3312"))
    it = next(i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
              if i["payload"]["item"]["cash_txn_id"] == "FX1")
    r = client.post("/api/v1/review-items/batch", json={"ids": [it["id"]], "action": "create_document",
                                                       "payload": {"category_code": "P01", "period_mode": "fixed", "period_key": "2026-08",
                                                                   "store_by_item": {it["id"]: ""}}})
    assert r.status_code == 200 and r.json()["ok"] == [it["id"]], r.text
    pl = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-2",
                                                    "columns": "month"}).json()
    p01 = [l for s in pl["sections"] for l in s["lines"] if l["category_code"] == "P01"]
    assert p01 and p01[0]["values"]["total"] == 14541


def test_batch_move_to_store_and_overview_shows_company_level(client, svc_headers):
    _send(client, svc_headers, _txn("MV1", 20000, counterparty_name="施駿緯", txn_date="2026-09-07", cash_account_id="acc-3312"))
    it = next(i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
              if i["payload"]["item"]["cash_txn_id"] == "MV1")
    client.post("/api/v1/review-items/batch", json={"ids": [it["id"]], "action": "create_document",
                                                   "payload": {"category_code": "P01", "period_mode": "fixed", "period_key": "2026-08",
                                                               "store_by_item": {it["id"]: ""}}})
    ov = client.get("/api/v1/pl/overview", params={"period": "2026-08"}).json()
    assert ov["stores_with_data"] == 0 and ov["unallocated_amount"] != 0      # 總覽要顯示公司層級
    doc = client.get("/api/v1/documents", params={"company_id": "comp-2", "period": "2026-08", "doc_type": "payable"}).json()["items"][0]
    r = client.post("/api/v1/documents/batch-store", json={"document_ids": [doc["id"]], "store_id": "store-ys-01"})
    assert r.status_code == 200 and r.json()["ok"] == [doc["id"]], r.text
    pl = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08", "company_id": "comp-2",
                                                    "store_id": "store-ys-01", "columns": "month"}).json()
    assert [l for s in pl["sections"] for l in s["lines"] if l["category_code"] == "P01"][0]["values"]["total"] == 20000
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["settle_status"] == "settled"


def test_batch_change_period(client, svc_headers):
    """選錯月份：應付單據一次改損益月份；已付狀態不變；已關帳的月份不能改進去。"""
    _send(client, svc_headers, _txn("PD1", 30000, counterparty_name="施駿緯", txn_date="2026-10-05", cash_account_id="acc-3312"))
    it = next(i for i in client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
              if i["payload"]["item"]["cash_txn_id"] == "PD1")
    client.post("/api/v1/review-items/batch", json={"ids": [it["id"]], "action": "create_document",
                                                   "payload": {"category_code": "P01", "period_mode": "same"}})
    doc = client.get("/api/v1/documents", params={"company_id": "comp-2", "period": "2026-10", "doc_type": "payable"}).json()["items"][0]
    r = client.post("/api/v1/documents/batch-period", json={"document_ids": [doc["id"]], "period_key": "2026-09"})
    assert r.status_code == 200 and r.json()["ok"] == [doc["id"]], r.text
    d = client.get(f"/api/v1/documents/{doc['id']}").json()
    assert d["period_keys"] == ["2026-09"] and d["settle_status"] == "settled"
    assert client.post("/api/v1/documents/batch-period", json={"document_ids": [doc["id"]], "period_key": "2026-13"}).status_code == 422
