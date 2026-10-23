from __future__ import annotations

import io

from openpyxl import Workbook


def payable(client, *, company="comp-1790322196308", store="store-msw-01", period="2026-08", amount=10500,
            category="C01", vendor="協昌果菜行", invoice_no=None, has_invoice=True, method="pending", memo=None,
            doc_date=None, lines=None):
    net = round(amount / 1.05) if has_invoice else amount
    body = {
        "doc_type": "payable", "company_id": company, "doc_date": doc_date or f"{period}-28",
        "counterparty_name": vendor, "invoice_no": invoice_no, "has_invoice": has_invoice,
        "settle_amount": amount, "tax_amount": amount - net, "settlement_method": method, "memo": memo,
        "lines": lines or [{"period_key": period, "category_code": category, "amount": net, "store_id": store}],
    }
    r = client.post("/api/v1/documents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
