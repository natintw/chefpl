"""過帳規則：金額檢查、重複發票、關帳、零用金、資本支出與折舊。"""
from app.feature.accruals import AccrualService
from tests.conftest import WH, WH_STORE
from tests.helpers import payable


def test_create_payable_sets_due_date_and_open(client):
    d = payable(client, amount=41850)
    assert d["settle_status"] == "open"
    assert d["due_date"] == "2026-09-30"          # 次月 30 日
    assert d["lines"][0]["amount"] == 39857       # 41850 ÷ 1.05
    assert d["tax_amount"] == 1993


def test_amount_mismatch_rejected(client):
    r = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-20", "counterparty_name": "協昌果菜行",
        "settle_amount": 10000, "lines": [{"period_key": "2026-08", "category_code": "C01", "amount": 9000}]})
    assert r.status_code == 422 and r.json()["code"] == "AMOUNT_MISMATCH"


def test_duplicate_invoice_rejected(client):
    payable(client, invoice_no="AB12345678")
    r = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-20", "counterparty_name": "協昌果菜行",
        "invoice_no": "AB12345678", "has_invoice": True, "settle_amount": 10500, "tax_amount": 500,
        "lines": [{"period_key": "2026-08", "category_code": "C01", "amount": 10000, "store_id": WH_STORE}]})
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_INVOICE"


def test_store_must_belong_to_company(client):
    r = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-20", "counterparty_name": "協昌果菜行",
        "settle_amount": 1000, "lines": [{"period_key": "2026-08", "category_code": "C01", "amount": 1000,
                                          "store_id": "store-ct-01"}]})
    assert r.status_code == 422


def test_petty_cash_is_settled(client):
    d = payable(client, amount=380, has_invoice=False, method="petty_cash", category="E02", vendor="全聯福利中心")
    assert d["settle_status"] == "settled" and d["settled_by"] == "petty_cash"


def test_closed_period_blocks_posting(client, session):
    from app.feature.repositories import OpsRepository
    OpsRepository(session).period(WH, "2026-07").status = "closed"
    session.commit()
    r = client.post("/api/v1/documents", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-07-20", "counterparty_name": "協昌果菜行",
        "settle_amount": 1000, "lines": [{"period_key": "2026-07", "category_code": "C01", "amount": 1000}]})
    assert r.status_code == 409 and r.json()["code"] == "PERIOD_CLOSED"


def test_check_suggests_month_split(client):
    r = client.post("/api/v1/documents/check", json={
        "doc_type": "payable", "company_id": WH, "doc_date": "2026-08-31", "counterparty_name": "管委會",
        "memo": "7+8月管理費", "settle_amount": 9600,
        "lines": [{"period_key": "2026-08", "category_code": "O03", "amount": 9600}]})
    codes = {f["code"]: f for f in r.json()["findings"]}
    assert codes["split_months"]["data"]["periods"] == ["2026-07", "2026-08"]


def test_capex_requires_life_and_depreciates(client, session):
    body = {"doc_type": "payable", "company_id": WH, "doc_date": "2026-08-10", "counterparty_name": "翔舜冷凍行",
            "has_invoice": True, "settle_amount": 126000, "tax_amount": 6000,
            "lines": [{"period_key": "2026-08", "category_code": "B01", "amount": 120000, "store_id": WH_STORE,
                       "description": "冷凍櫃"}]}
    assert client.post("/api/v1/documents", json=body).json()["code"] == "ASSET_LIFE_REQUIRED"
    body["asset_life_months"] = 36
    assert client.post("/api/v1/documents", json=body).status_code == 201

    AccrualService(session).run_recurring("2026-08")
    session.commit()
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-08", "period_to": "2026-08",
                                                     "company_id": WH}).json()
    occ = next(s for s in st["sections"] if s["section"] == "occupancy")
    assert occ["subtotal"]["total"] == 3333       # 120000 ÷ 36，資本支出本身不進損益
    bal = client.get("/api/v1/balances", params={"as_of": "2026-08-31", "company_id": WH}).json()
    b01 = next(b for c in bal["companies"] for b in c["balances"] if b["category_code"] == "B01")
    assert b01["amount"] == 120000 - 3333
