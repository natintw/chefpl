"""門市現金：銀行入款先建成「銀行」應收，POS 後來匯入同一筆 → 勾選配對、自動配對（備註寫的日期）。"""
from tests.test_bank_webhook import _send
from tests.test_pl import _receivable

ACC = "acc-006-tcb"   # 楊師傅
CASH = "門市現金（存入銀行）"


def _bank_doc(client, svc_headers, txn, date, amount, note):
    _send(client, svc_headers, {"cash_txn_id": txn, "cash_account_id": ACC, "txn_date": date, "direction": "in",
                                "amount": amount, "bank_memo": "現金存提", "bank_note": note, "counterparty_name": note})
    items = client.get("/api/v1/review-items", params={"kind": "bank_only"}).json()["items"]
    rv = next(i for i in items if i["payload"]["item"]["cash_txn_id"] == txn)
    r = client.post(f"/api/v1/review-items/{rv['id']}/resolve", json={"action": "create_document", "payload": {
        "category_code": "R05", "store_id": "store-ys-01", "counterparty_name": note}})
    assert r.status_code == 200, r.text
    return r.json()["document_id"]


def _pos(client, date, amount):
    r = client.post("/api/v1/documents", json={
        "doc_type": "receivable", "company_id": "comp-2", "doc_date": date, "counterparty_name": CASH,
        "settle_amount": amount, "source_kind": "manual",
        "lines": [{"period_key": date[:7], "category_code": "R01", "amount": amount, "store_id": "store-ys-01"}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_link_selected_pairs_by_amount(client, svc_headers):
    b23 = _bank_doc(client, svc_headers, "T-0924a", "2026-09-24", 51_333, "9/23")
    b22 = _bank_doc(client, svc_headers, "T-0924b", "2026-09-24", 57_960, "9/22")
    p22, p23 = _pos(client, "2026-09-22", 57_960), _pos(client, "2026-09-23", 51_333)
    plan = client.post("/api/v1/documents/link-plan", json={"ids": [b23, b22, p22, p23]}).json()
    pairs = {g["bank_document"]["id"]: [d["id"] for d in g["documents"]] for g in plan["groups"]}
    assert pairs == {b23: [p23], b22: [p22]} and all(g["difference"] == 0 for g in plan["groups"])
    r = client.post("/api/v1/documents/link-apply", json={"groups": [
        {"bank_document_id": g["bank_document"]["id"], "document_ids": [d["id"] for d in g["documents"]]} for g in plan["groups"]]})
    assert r.status_code == 200 and r.json()["linked"] == 2, r.text
    for b in (b23, b22):
        assert client.get(f"/api/v1/documents/{b}").json()["status"] == "void"
    p = client.get(f"/api/v1/documents/{p23}").json()
    assert p["settle_status"] == "settled" and p["settled_dates"] == ["2026-09-24"]


def test_auto_plan_uses_dates_in_bank_note(client, svc_headers):
    b = _bank_doc(client, svc_headers, "T-0921", "2026-09-21", 98_374, "9/15+9/16")
    lone = _bank_doc(client, svc_headers, "T-0924c", "2026-09-24", 449, "9/21")
    near = _bank_doc(client, svc_headers, "T-0926", "2026-09-26", 61_674, "存款")
    p15, p16 = _pos(client, "2026-09-15", 50_000), _pos(client, "2026-09-16", 48_374)
    _pos(client, "2026-09-17", 50_000)                         # 干擾：同金額但不是備註寫的日子
    p25 = _pos(client, "2026-09-25", 61_674)
    plan = client.post("/api/v1/documents/auto-link-plan", json={"company_id": "comp-2", "period": "2026-09"}).json()
    got = {g["bank_document"]["id"]: (g["how"], sorted(d["id"] for d in g["documents"])) for g in plan["groups"]}
    assert got[b] == ("note_date", sorted([p15, p16]))
    assert got[near] == ("same_amount_near", [p25])
    assert lone not in got and plan["skipped"] == 1


def test_link_plan_rejects_without_bank_or_pos(client, svc_headers):
    p = _pos(client, "2026-09-22", 100)
    r = client.post("/api/v1/documents/link-plan", json={"ids": [p]})
    assert r.status_code == 422
