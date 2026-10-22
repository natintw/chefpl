"""ImportParser（開發文件 4.4）：把 Excel 解析成「列」，不碰資料庫。

每種格式一個 parser，新增格式不改呼叫端。欄位名稱容許常見別名，找不到必要欄位就回報錯誤。

【待確認】實際的應付表與薪資表格式。目前以《標準範本》實作（見 README「匯入範本」），
拿到真實檔案後，只需調整 COLUMN_ALIASES 或新增 parser。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook

ID_NUMBER_RE = re.compile(r"^[A-Z][12]\d{8}$")
TAX_ID_RE = re.compile(r"^\d{8}$")
INVOICE_RE = re.compile(r"[A-Z]{2}\d{8}")


@dataclass
class ParsedRow:
    row: int
    values: dict[str, Any]
    warnings: list[dict] = field(default_factory=list)


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    errors: list[dict]
    warnings: list[dict]
    meta: dict[str, Any] = field(default_factory=dict)


def _norm(v: Any) -> str:
    return re.sub(r"\s+", "", str(v)) if v is not None else ""


def _find_header(ws, aliases: dict[str, list[str]], required: list[str], max_scan: int = 15):
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True), start=1):
        cells = [_norm(c) for c in row]
        mapping: dict[str, int] = {}
        for key, names in aliases.items():
            for i, c in enumerate(cells):
                # 兩個字的別名只接受完全相同（避免「發票」誤配到「發票金額」）
                if c and any(c == n or (len(n) >= 3 and c.startswith(n)) for n in names):
                    mapping.setdefault(key, i)
                    break
        if all(k in mapping for k in required):
            return r_idx, mapping
    return None, {}


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).replace(",", "").replace("$", "").strip()
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _to_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if not v:
        return None
    s = str(v).strip().replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{2,4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    y = int(m.group(1))
    y = y + 1911 if y < 1000 else y  # 民國年
    return date(y, int(m.group(2)), int(m.group(3)))


def mask_id_number(v: str) -> str:
    return v[:2] + "*****" + v[-3:]


# ---------------------------------------------------------------------- 應付表
AP_ALIASES = {
    "store": ["門市", "店別", "店名"],
    "vendor": ["廠商", "廠商名稱", "供應商", "對象"],
    "vendor_code": ["代號", "統編", "統一編號", "廠商代號"],
    "invoice_no": ["發票號碼", "發票"],
    "date": ["傳票日期", "日期", "發票日期"],
    "memo": ["摘要", "品名", "說明"],
    "amount": ["應付金額", "金額", "含稅金額"],
    "kind": ["類型"],
    "category": ["類別", "類別代碼"],
}


def parse_ap_sheet(data: bytes) -> ParseResult:
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    header_row, col = _find_header(ws, AP_ALIASES, ["vendor", "amount"])
    if header_row is None:
        return ParseResult([], [{"row": 0, "code": "NO_HEADER", "message": "找不到「廠商」與「金額」欄"}], [])

    rows: list[ParsedRow] = []
    errors: list[dict] = []
    warnings: list[dict] = []
    section = "cost"
    seen_separator = False
    for r_idx, raw in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        get = lambda k: raw[col[k]] if k in col and col[k] < len(raw) else None  # noqa: E731
        vendor = _norm(get("vendor"))
        joined = "".join(_norm(c) for c in raw if c is not None)
        if not joined:
            continue
        if "成本合計" in joined:
            section, seen_separator = "expense", True
            continue
        if any(k in vendor for k in ("合計", "總計", "小計")) or (not vendor and "合計" in joined):
            continue
        amount = _to_int(get("amount"))
        if not vendor or amount is None:
            errors.append({"row": r_idx, "code": "MISSING_FIELD", "message": "缺廠商或金額"})
            continue
        pr = ParsedRow(row=r_idx, values={
            "store": _norm(get("store")) or None, "vendor": vendor, "amount": amount,
            "memo": str(get("memo")).strip() if get("memo") else None,
            "date": _to_date(get("date")).isoformat() if _to_date(get("date")) else None,
            "kind": {"成本": "cost", "費用": "expense"}.get(_norm(get("kind")), section if "kind" not in col else None),
            "category": _norm(get("category")) or None,
        })
        code = _norm(get("vendor_code"))
        if ID_NUMBER_RE.match(code):
            pr.values["id_masked"] = mask_id_number(code)
            pr.warnings.append({"code": "ID_NUMBER", "message": "代號為身分證字號，已遮蔽"})
        elif TAX_ID_RE.match(code):
            pr.values["tax_id"] = code
        inv_raw = _norm(get("invoice_no"))
        if not inv_raw:
            pr.values["invoice_nos"], pr.values["has_invoice"] = [], False
            pr.warnings.append({"code": "NO_INVOICE", "message": "無發票號碼：進項不能扣抵"})
        elif "沒發票" in inv_raw or "無發票" in inv_raw:
            pr.values["invoice_nos"], pr.values["has_invoice"] = [], False
            pr.warnings.append({"code": "NOTED_NO_INVOICE", "message": "註明沒發票"})
        else:
            nos = INVOICE_RE.findall(inv_raw.upper())
            pr.values["invoice_nos"], pr.values["has_invoice"] = nos or [inv_raw], True
            if "+" in inv_raw or len(nos) > 1:
                pr.warnings.append({"code": "MULTI_INVOICE", "message": f"一格多張發票（{len(nos) or '?'} 張）"})
        rows.append(pr)
        warnings.extend({"row": r_idx, **w} for w in pr.warnings)
    if not seen_separator and "kind" not in col:
        warnings.append({"row": 0, "code": "NO_SEPARATOR", "message": "找不到「成本合計」分隔列，類型改用廠商預設"})
        for r in rows:
            r.values["kind"] = None
    return ParseResult(rows, errors, warnings, {"header_row": header_row})


# ---------------------------------------------------------------------- 薪資表（門市彙總）
PAYROLL_ALIASES = {
    "store": ["門市", "店別", "店名", "部門"],
    "P01": ["薪資", "本薪", "薪資合計"],
    "P02": ["加班與獎金", "加班費", "獎金"],
    "P03_labor": ["勞保雇主", "勞保公司負擔", "勞保"],
    "P03_health": ["健保雇主", "健保公司負擔", "健保"],
    "P04": ["勞退", "勞退提繳", "勞退6%"],
}


def parse_payroll(data: bytes) -> ParseResult:
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    header_row, col = _find_header(ws, PAYROLL_ALIASES, ["store", "P01"])
    if header_row is None:
        return ParseResult([], [{"row": 0, "code": "NO_HEADER", "message": "找不到「門市」與「薪資」欄"}], [])
    rows, errors = [], []
    for r_idx, raw in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        store = _norm(raw[col["store"]]) if col["store"] < len(raw) else ""
        if not store or any(k in store for k in ("合計", "總計")):
            continue
        vals: dict[str, int] = {}
        for key in ("P01", "P02", "P03_labor", "P03_health", "P04"):
            if key in col:
                vals[key] = _to_int(raw[col[key]]) or 0
        if not any(vals.values()):
            errors.append({"row": r_idx, "code": "EMPTY_ROW", "message": f"{store} 沒有金額"})
            continue
        rows.append(ParsedRow(row=r_idx, values={"store": store, **vals}))
    return ParseResult(rows, errors, [])


PARSERS = {"ap_sheet": parse_ap_sheet, "payroll": parse_payroll}
