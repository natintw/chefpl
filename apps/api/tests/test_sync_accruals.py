"""發票同步、預估沖回、分月預提、預收轉營收、月結。"""
import copy
import json
from pathlib import Path

from app.feature.accruals import AccrualService
from app.feature.invoice_sync import InvoiceSyncService
from app.seams import invoice_feed
from tests.conftest import WH, WH_STORE
from tests.helpers import payable

EVENT = json.loads((Path(__file__).parents[1] / "fixtures" / "invoice-events.json").read_text("utf-8"))[0]


def _ev(seq, kind, **inv):
    e = copy.deepcopy(EVENT)
    e["sequence"], e["event_type"] = seq, kind
    e["invoice"].update(inv)
    return e


def _sync(session):
    out = InvoiceSyncService(session).run()
    session.commit()
    return out


def test_invoice_sync_lifecycle(client, session):
    feed = invoice_feed.MockInvoiceFeed(events=[_ev(1, "confirmed")])
    invoice_feed.set_invoice_feed(feed)
    assert _sync(session)["created"] == 1
    assert _sync(session)["events"] == 0                       # 游標已前進，不重讀
    docs = client.get("/api/v1/documents", params={"source_kind": "invoice"}).json()["items"]
    # 新廠商沒有預設類別，但名稱「印刷」命中全域關鍵字 E07 → 直接入帳；新廠商進待確認
    assert docs[0]["settle_amount"] == 54285 and docs[0]["status"] == "posted"
    detail = client.get(f"/api/v1/documents/{docs[0]['id']}").json()
    assert detail["lines"][0]["category_code"] == "E07" and detail["lines"][0]["store_id"] == "store-bh-factory"
    assert any(i["kind"] == "new_counterparty" for i in client.get("/api/v1/review-items").json()["items"])

    feed.events.append(_ev(2, "updated", sales_amount=60000, tax_amount=3000, total_amount=63000))
    _sync(session)
    assert client.get(f"/api/v1/documents/{docs[0]['id']}").json()["settle_amount"] == 63000
    feed.events.append(_ev(3, "voided"))
    _sync(session)
    assert client.get(f"/api/v1/documents/{docs[0]['id']}").json()["status"] == "void"


def test_invoice_already_imported_goes_to_review(client, session):
    from app.feature.repositories import CatalogRepository
    cp = CatalogRepository(session).add_counterparty(name="富霖印刷", tax_id="12345675", default_category_code="E07")
    session.commit()
    payable(client, company="comp-1", store="store-bh-factory", vendor="富霖印刷", invoice_no="CB14993775",
            amount=54285, category="E07")
    invoice_feed.set_invoice_feed(invoice_feed.MockInvoiceFeed(events=[_ev(1, "confirmed")]))
    assert _sync(session)["review"] == 1
    assert len(client.get("/api/v1/documents", params={"company_id": "comp-1"}).json()["items"]) == 1
    assert cp.id


def test_estimate_then_reverse(client, session):
    from app.feature.models import AccrualTemplate
    from app.feature.repositories import CatalogRepository, OpsRepository
    cp = CatalogRepository(session).add_counterparty(name="台灣電力公司", default_category_code="O02")
    t = OpsRepository(session).add(AccrualTemplate(company_id=WH, store_id=WH_STORE, counterparty_id=cp.id,
                                                   category_code="O02", accrual_mode="estimate_reverse",
                                                   start_period="2026-01", description="電費"))
    session.commit()
    for m, amt in (("2026-05", 50400), ("2026-06", 52500), ("2026-07", 54600)):
        payable(client, period=m, amount=amt, vendor="台灣電力公司", category="O02")

    sug = client.get("/api/v1/accruals/suggestions", params={"company_id": WH, "period": "2026-08"}).json()["items"]
    assert len(sug) == 1 and sug[0]["suggested_amount"] == 50000   # (48000+50000+52000)/3
    est = client.post("/api/v1/accruals/batch", json={"company_id": WH, "period": "2026-08",
                                                       "items": [{"template_id": str(t.id), "amount": 50000}]}).json()
    acc_id = est["items"][0]["id"]
    assert client.get("/api/v1/accruals/suggestions", params={"company_id": WH, "period": "2026-08"}).json()["items"] == []

    actual = payable(client, period="2026-09", doc_date="2026-09-12", amount=56700, vendor="台灣電力公司",
                     category="O02")   # 實際帳單 9 月才到，未稅 54,000
    r = client.post(f"/api/v1/documents/{actual['id']}/link-accrual",
                    json={"accrual_id": acc_id, "relation": "reverses_accrual"}).json()
    assert r["journal"]["lines"][0]["amount"] == -50000

    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-09",
                                                     "company_id": WH, "columns": "month"}).json()
    occ = next(s for s in st["sections"] if s["section"] == "occupancy")["subtotal"]
    assert occ["2026-08"] == 50000 and occ["2026-09"] == 4000            # 8 月預估、9 月只多差額
    assert client.get("/integration/v1/open-items", headers={"Authorization": "Bearer sk_cashflow_ledger_test_key_0123456789abcdef"}).json()["items"][-1]["doc_type"] == "payable"


def test_provision_consumed_on_payout(client, session):
    from app.feature.models import AccrualTemplate
    from app.feature.repositories import OpsRepository
    t = OpsRepository(session).add(AccrualTemplate(company_id=WH, store_id=WH_STORE, category_code="P02",
                                                   accrual_mode="provision", estimate_method="fixed",
                                                   fixed_amount=10000, start_period="2026-10",
                                                   description="年終獎金預提"))
    session.commit()
    for m in ("2026-10", "2026-11", "2026-12"):
        AccrualService(session).run_recurring(m)
    session.commit()
    payout = payable(client, period="2027-01", amount=35000, vendor="員工薪資", category="P02", has_invoice=False)
    acc = client.get("/api/v1/documents", params={"doc_type": "accrual"}).json()["items"][0]
    r = client.post(f"/api/v1/documents/{payout['id']}/link-accrual",
                    json={"accrual_id": acc["id"], "relation": "consumes_provision"}).json()
    assert r["journal"]["lines"][0]["amount"] == -30000
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-10", "period_to": "2027-01",
                                                     "company_id": WH, "columns": "month"}).json()
    labor = next(s for s in st["sections"] if s["section"] == "labor")["subtotal"]
    assert [labor[m] for m in ("2026-10", "2026-11", "2026-12", "2027-01")] == [10000, 10000, 10000, 5000]
    assert t.id


def test_advance_receipt_recognized_on_delivery(client):
    adv = client.post("/api/v1/documents", json={
        "doc_type": "advance_receipt", "company_id": WH, "doc_date": "2026-12-10", "counterparty_name": "年菜預購",
        "settle_amount": 105000, "has_invoice": True, "expected_delivery_date": "2027-02-04", "memo": "年菜 50 套"})
    assert adv.status_code == 201, adv.text
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-12", "period_to": "2026-12",
                                                     "company_id": WH}).json()
    assert st["sections"][0]["subtotal"]["total"] == 0                    # 收款當月不進營收
    bad = client.post("/api/v1/advances/recognize", json={"items": [{"document_id": adv.json()["id"],
                      "amount": 200000, "period": "2027-02", "category_code": "R04"}]})
    assert bad.json()["code"] == "EXCEEDS_ADVANCE"
    ok = client.post("/api/v1/advances/recognize", json={"items": [{"document_id": adv.json()["id"],
                     "amount": 105000, "period": "2027-02", "category_code": "R04", "store_id": WH_STORE}]})
    assert ok.json()["items"][0]["lines"][0]["amount"] == 100000          # 未稅
    bal = client.get("/api/v1/balances", params={"as_of": "2027-02-28", "company_id": WH}).json()
    assert bal["companies"][0]["advance_balance"] == 0


def test_period_close(client, session):
    payable(client, amount=1050)
    chk = client.get(f"/api/v1/periods/{WH}/2026-08").json()["checklist"]
    status = {c["code"]: c["status"] for c in chk}
    assert status["revenue"] == "failed" and status["payroll"] == "failed"
    r = client.post(f"/api/v1/periods/{WH}/2026-08/close", json={})
    assert r.status_code == 422 and r.json()["code"] == "CHECKLIST_INCOMPLETE"

    # 補齊營收與薪資後關帳
    client.post("/api/v1/documents", json={
        "doc_type": "receivable", "company_id": WH, "doc_date": "2026-08-31", "counterparty_name": "POS 日結",
        "settle_amount": 5000, "lines": [{"period_key": "2026-08", "category_code": "R01", "amount": 5000,
                                          "store_id": WH_STORE}]})
    from tests.helpers import xlsx
    b = client.post("/api/v1/imports", data={"kind": "payroll", "company_id": WH, "period": "2026-08"},
                    files={"file": ("p.xlsx", xlsx([["門市", "薪資"], ["文衡店", 1000]]), "x")}).json()
    client.post(f"/api/v1/imports/{b['id']}/commit")
    for it in client.get("/api/v1/review-items").json()["items"]:
        client.post(f"/api/v1/review-items/{it['id']}/resolve", json={"action": "dismiss"})
    r = client.post(f"/api/v1/periods/{WH}/2026-08/close", json={})
    assert r.status_code == 200, r.text
    late = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-31", "counterparty_name": "協昌果菜行",
        "settle_amount": 100, "lines": [{"period_key": "2026-08", "category_code": "C01", "amount": 100}]})
    assert late.json()["code"] == "PERIOD_CLOSED"
    assert client.post(f"/api/v1/periods/{WH}/2026-08/reopen", json={"reason": "補帳"}).status_code == 200
