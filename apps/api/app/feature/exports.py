"""Excel 下載：損益表（與畫面同樣的列與欄＋明細）、應付／應收／預收清單（單據＋明細）。

內容與畫面上的篩選一致；金額為系統當下的數字（下載後不會再變），匯出時間寫在第一列。
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.feature.common import outstanding
from app.feature.models import Document
from app.feature.repositories import CatalogRepository
from app.seams.company import get_directory

FONT = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="1E3A5F")
SEC_FILL = PatternFill("solid", fgColor="E8EEF6")
SUBT_FILL = PatternFill("solid", fgColor="F3F4F6")
THIN = Side(style="thin", color="D1D5DB")
MONEY = '#,##0;(#,##0);"-"'
PCT = '0.0%;-0.0%;"-"'

DOC_TYPE = {"payable": "應付", "receivable": "應收", "advance_receipt": "預收"}
SOURCE = {"invoice": "發票", "invoice_summary": "發票彙總", "ap_sheet": "應付表", "payroll": "薪資", "pos": "POS",
          "platform": "平台", "b2b": "B2B", "recurring": "固定", "bank_only": "銀行", "allocation": "分攤", "manual": "人工",
          "accrual": "預估", "advance": "預收", "system": "系統", "opening": "開帳"}
SETTLE = {"open": "未{}", "partial": "部分{}款", "settled": "已{}", "over": "溢{}", "not_applicable": "不適用"}
STATUS = {"posted": "已入帳", "draft": "草稿", "void": "已作廢"}


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _title(ws, title: str, sub: str, ncols: int) -> None:
    ws.append([title])
    ws.append([f"{sub}　匯出時間 {_now()}"])
    ws["A1"].font = Font(name=FONT, size=14, bold=True)
    ws["A2"].font = Font(name=FONT, size=9, color="6B7280")
    ws.append([])


def _header(ws, headers: list[str]) -> int:
    ws.append(headers)
    r = ws.max_row
    for c in ws[r]:
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    return r


def _finish(ws, widths: list[int], header_row: int) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=header_row + 1, column=2)
    for row in ws.iter_rows(min_row=header_row + 1):
        for c in row:
            if c.font is None or c.font.name != FONT:
                c.font = Font(name=FONT, bold=c.font.bold if c.font else False)


def _bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 損益表
def statement_xlsx(session: Session, st: dict[str, Any], *, scope_label: str, mode: str,
                   company_ids: set[str] | None, store_id: str | None) -> bytes:
    """mode：store（依門市並列，欄＝門市＋合計）／month（依月份，欄＝上月、本月＋增減）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "損益表"
    cols = st["columns"]
    if mode == "month":
        cols = [c for c in cols if c["key"] != "total"]
    keys = [c["key"] for c in cols]
    headers = ["項目", *[c["label"] for c in cols]] + (["增減"] if mode == "month" else []) + ["佔營收"]
    sub = f"{st['period_from']}" + (f" ～ {st['period_to']}" if st["period_from"] != st["period_to"] else "") + "・金額未稅"
    _title(ws, f"{scope_label} 損益表", sub, len(headers))
    hr = _header(ws, headers)
    sec = {s["section"]: s for s in st["sections"]}
    revenue = sec.get("revenue", {}).get("subtotal", {})
    rev_key = keys[-1] if mode == "month" else "total"   # 佔營收：依門市看合計、依月份看本月

    def row(label: str, values: dict, style: str) -> None:
        vals = [values.get(k, 0) or 0 for k in keys]
        extra = [vals[-1] - vals[0]] if mode == "month" else []
        r = revenue.get(rev_key) or 0
        share = (values.get(rev_key, 0) or 0) / r if r else None
        ws.append([label, *vals, *extra, share])
        cur = ws[ws.max_row]
        for c in cur[1:len(keys) + 1 + len(extra)]:
            c.number_format = MONEY
        cur[-1].number_format = PCT
        bold = style in ("sec", "subt", "big")
        fill = SEC_FILL if style == "sec" else SUBT_FILL if style in ("subt", "big") else None
        for c in cur:
            c.font = Font(name=FONT, bold=bold, size=12 if style == "big" else 11)
            if fill:
                c.fill = fill
            if style in ("subt", "big"):
                c.border = Border(top=THIN)
        if style == "line":
            cur[0].alignment = Alignment(indent=1)

    def push(s: str) -> None:
        x = sec.get(s)
        if not x:
            return
        row(x["label"], x["subtotal"], "sec")
        for ln in x["lines"]:
            row(f"{ln['category_code']} {ln['label']}", ln["values"], "line")

    push("revenue"); push("cogs")
    row("毛利", st["subtotals"]["gross_profit"], "subt")
    for s in ("labor", "occupancy", "opex", "channel"):
        push(s)
    row("門市營業利益（四牆利潤）", st["subtotals"]["store_operating_profit"], "big")
    if sec.get("allocation", {}).get("lines"):
        push("allocation")
    row("門市淨利", st["subtotals"]["store_net_profit"], "subt")
    if sec.get("non_operating", {}).get("lines"):
        push("non_operating")
        row("本期淨利", st["subtotals"]["net_profit"], "subt")
    ws.append([])
    for k, label in (("food_cost_pct", "Food cost（銷貨成本／營收）"), ("labor_cost_pct", "Labor cost（人事／營收）"),
                     ("prime_cost_pct", "Prime cost（銷貨成本＋人事／營收）")):
        vals = [(st["kpis"].get(c, {}) or {}).get(k) for c in keys]
        ws.append([label, *[v / 100 if v is not None else None for v in vals]])
        for c in ws[ws.max_row][1:]:
            c.number_format = PCT
    _finish(ws, [34] + [16] * (len(headers) - 1), hr)

    _pl_detail(wb, session, st["period_from"], st["period_to"], company_ids, store_id)
    return _bytes(wb)


def _pl_detail(wb: Workbook, session: Session, pf: str, pt: str, company_ids: set[str] | None, store_id: str | None) -> None:
    """第二頁：組成損益表的每一筆明細（會計核對用）。"""
    ws = wb.create_sheet("明細")
    d = get_directory()
    cats = CatalogRepository(session).categories
    where = ["v.period_key BETWEEN :pf AND :pt"]
    params: dict[str, Any] = {"pf": pf, "pt": pt}
    if company_ids is not None:
        where.append("v.company_id = ANY(:cids)")
        params["cids"] = list(company_ids)
    if store_id == "unallocated":
        where.append("v.store_id IS NULL")
    elif store_id:
        where.append("v.store_id = :sid")
        params["sid"] = store_id
    rows = session.execute(text(f"""
        SELECT v.period_key, v.doc_date, v.company_id, v.store_id, v.category_code, v.amount, v.source_kind,
               v.is_intercompany, cp.name AS counterparty, doc.invoice_nos, doc.memo, v.description
          FROM ledger.v_pl_lines v
          JOIN ledger.documents doc ON doc.id = v.document_id
          LEFT JOIN ledger.counterparties cp ON cp.id = doc.counterparty_id
         WHERE {' AND '.join(where)}
         ORDER BY v.period_key, v.category_code, v.doc_date"""), params).mappings().all()
    _title(ws, "損益明細", f"{pf}" + (f" ～ {pt}" if pf != pt else "") + "・金額未稅", 12)
    hr = _header(ws, ["損益月份", "單據日期", "公司", "門市", "類別代碼", "類別", "對象", "金額（未稅）", "來源", "發票號碼",
                      "摘要", "集團內"])
    for r in rows:
        c = d.get_company(r["company_id"])
        s = d.get_store(r["store_id"]) if r["store_id"] else None
        cat = cats.get(r["category_code"])
        ws.append([r["period_key"], r["doc_date"], c.name if c else r["company_id"], s.name if s else "公司層級",
                   r["category_code"], cat.name if cat else "", r["counterparty"] or "", r["amount"],
                   SOURCE.get(r["source_kind"], r["source_kind"]), " ".join(r["invoice_nos"] or []),
                   r["memo"] or r["description"] or "", "是" if r["is_intercompany"] else ""])
        ws.cell(ws.max_row, 2).number_format = "yyyy-mm-dd"
        ws.cell(ws.max_row, 8).number_format = MONEY
    _finish(ws, [10, 12, 24, 14, 8, 14, 28, 14, 10, 16, 36, 8], hr)


# ---------------------------------------------------------------- 應付／應收／預收
def documents_xlsx(docs: list[Document], *, doc_type: str | None, scope_label: str, period: str | None,
                   category_names: dict[str, str]) -> bytes:
    d = get_directory()
    kind = DOC_TYPE.get(doc_type or "", "單據")
    verb = "收" if doc_type in ("receivable", "advance_receipt") else "付"
    wb = Workbook()
    ws = wb.active
    ws.title = kind
    _title(ws, f"{scope_label} {kind}帳款", f"{period or '全部月份'}・共 {len(docs)} 張", 16)
    hr = _header(ws, ["單據日期", "損益月份", "公司", "門市", "對象", "統一編號", "發票號碼", "來源", "未稅金額", "稅額",
                      "含稅金額", f"已{verb}", f"未{verb}", "到期日", f"{verb}款狀態", "摘要"])
    first = ws.max_row + 1
    for doc in docs:
        c = d.get_company(doc.company_id)
        stores = sorted({ln.store_id for ln in doc.lines if ln.store_id})
        names = "、".join((d.get_store(x).name if d.get_store(x) else x) for x in stores) or "公司層級"
        net = sum(ln.amount for ln in doc.lines)
        total = doc.settle_amount or 0
        ws.append([doc.doc_date, "、".join(sorted({ln.period_key for ln in doc.lines})), c.name if c else doc.company_id, names,
                   doc.counterparty.name if doc.counterparty else "", (doc.counterparty.tax_id if doc.counterparty else None) or "",
                   " ".join(doc.invoice_nos or []), SOURCE.get(doc.source_kind, doc.source_kind), net, doc.tax_amount or 0,
                   total, (doc.settled_amount or 0) + (doc.offset_amount or 0), outstanding(doc),
                   doc.due_date, SETTLE.get(doc.settle_status, doc.settle_status).format(verb), doc.memo or ""])
        r = ws.max_row
        ws.cell(r, 1).number_format = "yyyy-mm-dd"
        ws.cell(r, 14).number_format = "yyyy-mm-dd"
        ws.cell(r, 6).number_format = "@"
        for col in range(9, 14):
            ws.cell(r, col).number_format = MONEY
    last = ws.max_row
    ws.append(["合計", *[""] * 7, *[f"=SUM({get_column_letter(col)}{first}:{get_column_letter(col)}{last})" if last >= first else 0
                                   for col in range(9, 14)]])
    for c in ws[ws.max_row]:
        c.font = Font(name=FONT, bold=True)
        c.fill = SUBT_FILL
        c.border = Border(top=THIN)
        if c.column >= 9:
            c.number_format = MONEY
    _finish(ws, [12, 12, 24, 18, 28, 11, 16, 9, 13, 11, 13, 13, 13, 12, 10, 40], hr)

    # 第二頁：明細（一張單據可能分到多個月份、門市、類別）
    ws2 = wb.create_sheet("明細")
    _title(ws2, f"{kind}明細", "一張單據拆成多行時，每行一列・金額未稅", 9)
    hr2 = _header(ws2, ["單據日期", "對象", "發票號碼", "損益月份", "門市", "類別代碼", "類別", "金額（未稅）", "說明"])
    for doc in docs:
        for ln in doc.lines:
            s = d.get_store(ln.store_id) if ln.store_id else None
            ws2.append([doc.doc_date, doc.counterparty.name if doc.counterparty else "", " ".join(doc.invoice_nos or []),
                        ln.period_key, s.name if s else "公司層級", ln.category_code,
                        category_names.get(ln.category_code, ""), ln.amount, ln.description or ""])
            ws2.cell(ws2.max_row, 1).number_format = "yyyy-mm-dd"
            ws2.cell(ws2.max_row, 8).number_format = MONEY
    _finish(ws2, [12, 28, 16, 10, 16, 9, 14, 13, 36], hr2)
    return _bytes(wb)
