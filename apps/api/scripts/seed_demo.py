"""示範資料：對「正在跑的」API 打請求，建立兩個月的單據、一次匯入、幾筆現金流回寫，讓前端每個畫面都有東西看。

用法（本機或 staging，AUTH_MODE=dev 時）：
    API=http://127.0.0.1:8080 CASHFLOW_KEY=demo-key python scripts/seed_demo.py [2026-09]

CASHFLOW_KEY 要和伺服器 INTEGRATION_CLIENTS 裡 cashflow 的 key_sha256 對得上；沒給就略過結清與銀行獨有項目。
所有金額都是虛構示意，不是真實帳。不要對正式資料庫執行。
"""
from __future__ import annotations

import io
import os
import sys
import uuid

import httpx
from openpyxl import Workbook

API = os.environ.get("API", "http://127.0.0.1:8080").rstrip("/")
KEY = os.environ.get("CASHFLOW_KEY")
H = {"X-Dev-User": os.environ.get("DEV_USER", "bill@chefteng.com")}
CUR = sys.argv[1] if len(sys.argv) > 1 else "2026-09"
y, m = map(int, CUR.split("-"))
PREV = f"{y if m > 1 else y - 1}-{(m - 1 or 12):02d}"

MS, MSH, CT, BH = "comp-1790322196308", "comp-1790263124662", "comp-1790263102815", "comp-1"
COMP = {"store-ms-wenheng": MS, "store-ms-main": MSH}
c = httpx.Client(base_url=API, headers=H, timeout=30)


def doc(company, period, lines, *, vendor=None, doc_type="payable", amount=None, invoice=True, method="pending",
        memo=None, due=None, day=25, **extra):
    net = sum(l[1] for l in lines)
    tax = round(net * 0.05) if invoice and doc_type in ("payable", "receivable") else 0
    body = {"doc_type": doc_type, "company_id": company, "doc_date": f"{period}-{day:02d}",
            "counterparty_name": vendor, "has_invoice": invoice, "settle_amount": amount or net + tax,
            "tax_amount": tax, "settlement_method": method, "memo": memo, "due_date": due,
            "lines": [{"period_key": period, "category_code": cat, "amount": amt, "store_id": st}
                      for cat, amt, st in lines], **extra}
    r = c.post("/api/v1/documents", json=body)
    if r.status_code != 201:
        print("  ✗", vendor, r.status_code, r.text[:200])
        return None
    return r.json()


def month(period: str, scale: float = 1.0, *, with_payroll=True):
    s = lambda n: int(n * scale)  # noqa: E731
    # 營收（應收：POS 日結示意）
    for st, base in (("store-ms-wenheng", 1_380_000), ("store-ms-main", 960_000)):
        doc(COMP[st], period, [("R01", s(base * .62), st), ("R02", s(base * .18), st), ("R03", s(base * .2), st)],
            vendor="POS 日結", doc_type="receivable", invoice=False, method="bank_paid", memo="POS 月結")
    for st, base in (("store-ct-01", 820_000), ("store-ct-02", 640_000)):
        doc(CT, period, [("R01", s(base * .7), st), ("R03", s(base * .3), st)], vendor="POS 日結",
            doc_type="receivable", invoice=False, method="bank_paid", memo="POS 月結")
    # 進貨
    for comp, st, k in ((MS, "store-ms-wenheng", 1.0), (MSH, "store-ms-main", .7), (CT, "store-ct-01", .6), (CT, "store-ct-02", .48)):
        doc(comp, period, [("C01", s(118_000 * k), st)], vendor="協昌果菜行", due=f"{period}-28")
        doc(comp, period, [("C02", s(196_000 * k), st)], vendor="豬肉黃", due=f"{period}-28")
        doc(comp, period, [("C03", s(64_000 * k), st)], vendor="元華行")
        doc(comp, period, [("O01", s(180_000 * k), st)], vendor="房東", invoice=False, method="bank_paid", memo="租金")
        doc(comp, period, [("O02", s(52_000 * k), st)], vendor="台灣電力公司", memo="電費")
        doc(comp, period, [("E10", s(48_000 * k), st)], vendor="Uber Eats", memo="平台佣金")
    doc(MS, period, [("E09", s(36_000), None)], vendor="富霖印刷", memo="菜單與 DM")
    doc(CT, period, [("E06", s(12_400), "store-ct-01")], vendor="大同文具行", invoice=False, method="petty_cash", memo="零用金")
    if with_payroll:
        for comp, st, base in ((MS, "store-ms-wenheng", 312_000), (MSH, "store-ms-main", 228_000),
                               (CT, "store-ct-01", 205_000), (CT, "store-ct-02", 168_000)):
            doc(comp, period, [("P01", s(base), st), ("P03", s(base * .11), st), ("P04", s(base * .06), st)],
                vendor="薪資", invoice=False, method="bank_paid", memo=f"{period} 薪資")


print(f"→ {API}  {PREV} 與 {CUR}")
month(PREV, 0.96)
month(CUR, 1.0, with_payroll=False)   # 本月故意不匯薪資，讓月結清單有一項沒過

# 本月薪資改用匯入（示範 S7）：文衡店已匯，其他店留給畫面操作
wb = Workbook(); ws = wb.active
ws.append(["門市", "薪資", "加班費", "勞保雇主", "健保雇主", "勞退"])
ws.append(["文衡店", 318_000, 22_000, 21_000, 14_000, 19_000])
buf = io.BytesIO(); wb.save(buf)
r = c.post("/api/v1/imports", data={"kind": "payroll", "company_id": MS, "period": CUR},
           files={"file": ("payroll.xlsx", buf.getvalue())})
if r.status_code == 201 and not r.json()["errors"]:
    print("  薪資匯入", c.post(f"/api/v1/imports/{r.json()['id']}/commit").status_code)
else:
    print("  薪資匯入預覽有問題（格式待 Bill 提供後調整）：", r.status_code, r.text[:300])

# 預收（年菜訂金）與資本支出（B01，不入損益）
doc(BH, CUR, [], vendor="年菜預購", doc_type="advance_receipt", invoice=False, amount=380_000,
    expected_delivery_date=f"{y + 1}-01-20", memo="年菜訂金")
doc(MS, CUR, [("B01", 450_000, "store-ms-wenheng")], vendor="冷凍設備行", memo="冷凍櫃", asset_life_months=60)
# 預估入帳：水電還沒來單
doc(CT, CUR, [("O02", 30_000, "store-ct-02")], vendor="台灣電力公司", doc_type="accrual", invoice=False,
    accrual_mode="estimate_reverse", memo="電費預估")
# 應付表匯入，其中一家新廠商規則沒命中 → 進待確認
wb = Workbook(); ws = wb.active
ws.append(["門市", "廠商", "發票號碼", "日期", "摘要", "金額"])
ws.append(["門市一［暫定］", "阿明水電行", "", f"{CUR}-18", "廚房水管", 8_600])
ws.append(["門市一［暫定］", "協昌果菜行", "AB12345678", f"{CUR}-27", "蔬菜補貨", 15_750])
buf = io.BytesIO(); wb.save(buf)
r = c.post("/api/v1/imports", data={"kind": "ap_sheet", "company_id": CT, "period": CUR},
           files={"file": ("ap.xlsx", buf.getvalue())})
print("  應付表匯入", r.status_code, c.post(f"/api/v1/imports/{r.json()['id']}/commit").status_code if r.status_code == 201 else r.text[:200])

if KEY:
    svc = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
    oi = svc.get("/integration/v1/open-items", params={"company_id": MS, "doc_type": "payable"}).json().get("items", [])
    items = [{"document_id": o["document_id"], "cash_txn_id": f"demo-{i}", "cash_account_id": "acct-ms-1",
              "txn_date": f"{CUR}-28", "amount": o["outstanding"], "match_level": "exact"}
             for i, o in enumerate(oi[:6])]
    if items:
        print("  結清", svc.post("/integration/v1/settlements", json={"items": items},
                                headers={"Idempotency-Key": str(uuid.uuid4())}).status_code)
    print("  銀行獨有", svc.post("/integration/v1/bank-only-items", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"items": [
        {"cash_txn_id": "demo-bo-1", "cash_account_id": "acct-ms-1", "company_id": MS, "txn_date": f"{CUR}-15",
         "direction": "out", "amount": 1_200, "bank_memo": "跨行手續費", "suggested_category_code": "E12"},
        {"cash_txn_id": "demo-bo-2", "cash_account_id": "acct-ct-1", "company_id": CT, "txn_date": f"{CUR}-20",
         "direction": "out", "amount": 26_500, "bank_memo": "轉帳 0820", "bank_note": "看不出對象"}]}).status_code)

# 發票同步（mock 事件）
print("  發票同步：另跑 python -m app.worker sync-invoices")
print("完成")
