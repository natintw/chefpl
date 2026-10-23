"""匯入流程（開發文件 5.3）：上傳 → 解析預覽（不入帳）→ 確認 commit → 建立單據。

應付表、薪資表：同一公司、同一月份、同一種匯入再 commit 一次，舊批次的單據作廢、批次標為 superseded
（已有付款的單據不能被取代）。
發票系統「廠商應付彙總表」（invoice_summary）：一個檔案涵蓋所有公司（批次的 company_id 為 *），
逐列比對：同一列（公司＋門市＋廠商＋發票月份＋入帳月份）已匯入且金額相同就略過，金額不同就以新的取代。
"""
from __future__ import annotations

import re

import uuid
from datetime import date, datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import HasErrors, NotFound, ValidationFailed
from app.feature.classification import classify
from app.feature.common import (PERIOD_RE, DocumentInput, LineInput, last_day, months_in_memo, split_evenly,
                                untaxed)
from app.feature.imports.parsers import PARSERS, ParsedRow, looks_like_invoice_summary
from app.feature.models import ImportBatch
from app.feature.posting import PostingService
from app.feature.review import _input_from_json, input_to_json
from app.seams.company import get_directory
from app.seams.files import get_file_store

log = logging.getLogger("ledger.imports")

PAYROLL_COUNTERPARTY = "員工薪資"
MULTI_COMPANY = "*"  # 一個批次涵蓋多家公司（發票彙總表）


class ImportService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.ops, self.catalog = self.posting.docs, self.posting.ops, self.posting.catalog
        self.dir = get_directory()
        self.cfg = get_settings()

    # ------------------------------------------------------------------ 上傳＋解析
    def upload(self, *, kind: str, company_id: str, period: str, store_id: str | None, file_name: str,
               data: bytes, actor: str) -> ImportBatch:
        if kind not in PARSERS:
            raise ValidationFailed(f"不支援的匯入類型：{kind}")
        if not PERIOD_RE.match(period):
            raise ValidationFailed("月份格式為 YYYY-MM")
        if kind == "ap_sheet" and looks_like_invoice_summary(data):
            kind = "invoice_summary"  # 選了應付表但上傳的是發票系統的彙總表：自動切換
        if kind == "invoice_summary":
            company_id = MULTI_COMPANY
        elif self.dir.get_company(company_id) is None:
            raise ValidationFailed("公司不存在")
        if len(data) > self.cfg.max_upload_mb * 1024 * 1024:
            raise ValidationFailed(f"檔案超過 {self.cfg.max_upload_mb} MB")
        store_warning = None
        try:  # 保存原檔只是備查，存不了也不能擋住匯入
            key = get_file_store().put("imports/", file_name, data,
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:  # noqa: BLE001
            log.exception("import original file not stored")
            key = ""
            store_warning = {"row": 0, "code": "FILE_NOT_STORED",
                             "message": f"原始檔案沒有保存（{type(e).__name__}），不影響入帳；請通知系統管理者檢查檔案儲存設定"}
        try:
            result = PARSERS[kind](data)
        except Exception as e:  # noqa: BLE001
            result = None
            err = [{"row": 0, "code": "UNREADABLE", "message": f"這個檔案不是支援的格式（{type(e).__name__}）"}]
        batch = ImportBatch(kind=kind, company_id=company_id, period_key=period, file_name=file_name, file_url=key,
                            status="parsed", imported_by=actor)
        if result is None:
            batch.status, batch.errors, batch.parsed, batch.warnings = "failed", err, [], []
            return self.ops.add_batch(batch)

        self.ops.add_batch(batch)
        builder = {"ap_sheet": self._ap_row, "payroll": self._payroll_row, "invoice_summary": self._summary_row}[kind]
        parsed, errors, warnings = [], list(result.errors), list(result.warnings)
        if store_warning:
            warnings.insert(0, store_warning)
        for r in result.rows:
            try:
                inp, extra = builder(r, batch, store_id, actor)
            except ValidationFailed as e:
                errors.append({"row": r.row, "code": e.code, "message": e.message})
                continue
            parsed.append({"row": r.row, "input": input_to_json(inp), **extra})
            warnings.extend({"row": r.row, **w} for w in extra.get("warnings", []))
        batch.parsed, batch.errors, batch.warnings = parsed, errors, warnings
        batch.row_count = len(parsed)
        batch.total_amount = sum(p["input"]["settle_amount"] for p in parsed if p.get("action") != "skip")
        return batch

    def _store(self, name: str | None, company_id: str, default: str | None) -> str | None:
        if not name:
            return default
        # 先找同一家公司的門市，再放寬到同一法人（總公司與分公司）
        candidates = sorted((st for st in self.dir.list_stores() if self.dir.same_legal_entity(st.company_id, company_id)),
                            key=lambda st: st.company_id != company_id)
        for st in candidates:  # 完全相符：名稱、代號、ID
            if name in (st.name, st.code, st.id):
                return st.id
        core = store_core(name)  # 寬鬆相符：「文衡店」＝「文衡門市」＝「文衡」
        hits = [st for st in candidates if core and store_core(st.name) == core]
        same_company = [st for st in hits if st.company_id == company_id]
        if len(same_company) == 1:
            return same_company[0].id
        if len(hits) == 1:
            return hits[0].id
        raise ValidationFailed(f"找不到門市「{name}」" if not hits else f"門市「{name}」對到不只一家，請改用完整名稱或代號")

    def _ap_row(self, r: ParsedRow, batch: ImportBatch, store_id: str | None, actor: str):
        v = r.values
        period = batch.period_key
        extra_w: list[dict] = []
        cp = self.catalog.find_counterparty(v["vendor"], v.get("tax_id"))
        if cp is None:
            cp = self.posting.ensure_counterparty(v["vendor"], v.get("tax_id"), batch.company_id, actor)
            if v.get("id_masked"):
                cp.id_masked = v["id_masked"]
            extra_w.append({"code": "NEW_COUNTERPARTY", "message": f"新廠商「{v['vendor']}」，已建立並列入待確認"})
        if cp.is_intercompany:
            extra_w.append({"code": "INTERCOMPANY", "message": "集團內交易，月結時需與對方公司核對"})

        if v.get("category"):
            code, decided, suggestion = v["category"], True, None
            if self.catalog.category(code) is None:
                raise ValidationFailed(f"類別代碼 {code} 不存在")
        else:
            c = classify(self.catalog, cp, v.get("memo"), "expense", v["amount"])
            code, decided, suggestion = c.category_code, c.decided, c.suggestion
            if not decided:
                extra_w.append({"code": "UNCLASSIFIED", "message": "分類規則沒有命中，入帳後進待確認"})
        if decided and v.get("kind"):
            sec = self.catalog.category(code).pl_section
            if (v["kind"] == "cost") != (sec == "cogs") and sec != "balance":
                extra_w.append({"code": "KIND_CONFLICT", "message": f"原表為{'成本' if v['kind'] == 'cost' else '費用'}，"
                                                                   f"但類別 {code} 屬於另一類；以類別為準"})

        has_inv = v.get("has_invoice", False)
        net = untaxed(v["amount"], has_inv, self.cfg.tax_rate)
        tax = v["amount"] - net
        doc_date = date.fromisoformat(v["date"]) if v.get("date") else last_day(period)
        months = months_in_memo(v.get("memo"), period) or [period]
        if len(months) > 1:
            extra_w.append({"code": "MULTI_MONTH", "message": f"跨月費用，已拆成 {'、'.join(months)}"})
        store = self._store(v.get("store"), batch.company_id, store_id)
        lines = [LineInput(m, code or "B99", amt, store, v.get("memo"))
                 for m, amt in zip(months, split_evenly(net, len(months)))]
        inp = DocumentInput(
            doc_type="payable", company_id=batch.company_id, doc_date=doc_date, source_kind="ap_sheet",
            source_ref=f"{batch.id}:{r.row}", counterparty_id=cp.id, invoice_nos=v.get("invoice_nos", []),
            has_invoice=has_inv, tax_amount=tax, settle_amount=v["amount"], memo=v.get("memo"),
            import_batch_id=batch.id, status="posted" if decided else "draft", lines=lines)
        return inp, {"warnings": extra_w, "suggestion": suggestion, "vendor": v["vendor"]}

    def _summary_row(self, r: ParsedRow, batch: ImportBatch, store_id: str | None, actor: str):
        """發票系統「廠商應付彙總表」的一列 → 一張應付單（含稅合計，未稅依 5% 還原）。"""
        v = r.values
        extra_w: list[dict] = []
        company = self.dir.get_by_tax_id(v["company_tax_id"])
        if company is None:
            raise ValidationFailed(f"找不到統編 {v['company_tax_id']}（{v.get('company_name') or '未填名稱'}）的公司，"
                                   f"請先在公司主檔補上統編")
        store_name = v.get("store")
        if v.get("store_code") and "/" in v["store_code"] and v["store_code"].split("/", 1)[1] in ("總部", "公司"):
            store_name = store_name or None
        store = self._store(store_name, company.id, None)
        if store:
            st = self.dir.get_store(store)
            if st and st.company_id != company.id:
                extra_w.append({"code": "STORE_OTHER_COMPANY",
                                "message": f"門市「{store_name}」在主檔屬於{self.dir.get_company(st.company_id).name}，"
                                           f"但發票開給{company.name}（同一法人，以發票為準記在{company.name}）"})

        cp = None
        if v.get("id_masked"):
            cp = self.catalog.counterparty_by_masked(v["id_masked"], v["vendor"])
        if cp is None:
            cp = self.catalog.find_counterparty(v["vendor"], v.get("tax_id"))
        if cp is None:
            # 新廠商不另開「新對象」待確認：沒有類別的會以「分類」待確認出現，到「廠商類別」一次設定
            cp = self.posting.ensure_counterparty(v["vendor"], v.get("tax_id"), company.id, actor, review=False)
            if v.get("id_masked"):
                cp.id_masked = v["id_masked"]
            extra_w.append({"code": "NEW_COUNTERPARTY", "message": f"新廠商「{v['vendor']}」，請到「廠商類別」設定類別"})
        elif v["vendor"] != cp.name and v["vendor"] not in (cp.aliases or []):
            cp.aliases = [*(cp.aliases or []), v["vendor"]]  # 同統編不同寫法（發票辨識誤差），記成別名
        if cp.is_intercompany:
            extra_w.append({"code": "INTERCOMPANY", "message": "集團內交易，月結時需與對方公司核對"})

        c = classify(self.catalog, cp, None, "expense", v["amount"])
        code, decided, suggestion = c.category_code, c.decided, c.suggestion
        if not decided:
            extra_w.append({"code": "UNCLASSIFIED", "message": "廠商還沒有對應類別，入帳後先記成草稿；到「廠商類別」設定後自動補入帳"})

        has_inv = bool(v.get("tax_id"))  # 個人（身分證字號）或沒有統編的視為無發票，不還原稅額
        net = untaxed(v["amount"], has_inv, self.cfg.tax_rate)
        period, inv_month = v["period"], v["invoice_month"]
        ref_vendor = v.get("tax_id") or v.get("id_masked") or v["vendor"]
        source_ref = f"{company.id}:{store or '-'}:{ref_vendor}:{inv_month}:{period}"
        memo = f"{inv_month} 發票 {v['invoice_count']} 張" if v.get("invoice_count") else f"{inv_month} 發票"
        due = date.fromisoformat(v["due"]) if v.get("due") else None
        inp = DocumentInput(
            doc_type="payable", company_id=company.id, doc_date=last_day(inv_month), source_kind="invoice_summary",
            source_ref=source_ref, counterparty_id=cp.id, has_invoice=has_inv, tax_amount=v["amount"] - net,
            settle_amount=v["amount"], due_date=due, memo=memo, import_batch_id=batch.id,
            status="posted" if decided else "draft",
            lines=[LineInput(period, code or "B99", net, store, memo)])

        action = "create"
        existing = self.docs.get_by_source("invoice_summary", source_ref)
        if existing is not None:
            if existing.settle_amount == v["amount"]:
                action = "skip"
                extra_w.append({"code": "ALREADY_IMPORTED", "message": "這一列先前已匯入，金額相同，略過"})
            elif existing.settled_amount:
                raise ValidationFailed(f"這一列先前已匯入且已付款（{existing.settle_amount:,} → {v['amount']:,}），"
                                       f"請改用個別修改")
            else:
                action = "replace"
                extra_w.append({"code": "AMOUNT_CHANGED",
                                "message": f"先前匯入 {existing.settle_amount:,}，這次 {v['amount']:,}，入帳時以新的取代"})
        if action == "skip":  # 已匯入過的列只留這一則提醒，其他（新廠商、未分類）先前就處理過了
            extra_w = [w for w in extra_w if w["code"] == "ALREADY_IMPORTED"]
        elif period != batch.period_key:
            extra_w.append({"code": "OTHER_PERIOD", "message": f"入帳月份是 {period}，會記在 {period}"})
        return inp, {"warnings": extra_w, "suggestion": suggestion, "vendor": cp.name, "action": action,
                     "replaces": str(existing.id) if action == "replace" else None}

    def _payroll_row(self, r: ParsedRow, batch: ImportBatch, store_id: str | None, actor: str):
        v = r.values
        period = batch.period_key
        store = None if v["store"] in ("公司層級", "總部", "辦公室") else self._store(v["store"], batch.company_id, None)
        cp = self.posting.ensure_counterparty(PAYROLL_COUNTERPARTY, None, batch.company_id, actor, roles=["vendor"])
        cp.payment_rule = "next_month_5"
        parts = [("P01", v.get("P01", 0), "薪資"), ("P02", v.get("P02", 0), "加班與獎金"),
                 ("P03", v.get("P03_labor", 0) + v.get("P03_health", 0), "勞健保雇主負擔"),
                 ("P04", v.get("P04", 0), "勞退提繳")]
        lines = [LineInput(period, code, amt, store, label) for code, amt, label in parts if amt]
        total = sum(l.amount for l in lines)
        inp = DocumentInput(
            doc_type="payable", company_id=batch.company_id, doc_date=last_day(period), source_kind="payroll",
            source_ref=f"{batch.company_id}:{store or 'company'}:{period}", counterparty_id=cp.id,
            settle_amount=total,  # 到期日依「員工薪資」的付款條件：次月 5 日
            memo=f"{period} 薪資（{v['store']}）", import_batch_id=batch.id, lines=lines)
        return inp, {"warnings": []}

    # ------------------------------------------------------------------ 確認入帳
    def commit(self, batch_id: uuid.UUID, actor: str) -> dict[str, Any]:
        batch = self.ops.batch(batch_id)
        if batch is None:
            raise NotFound("找不到匯入批次")
        if batch.status != "parsed":
            raise ValidationFailed(f"這個批次狀態為 {batch.status}，不能再入帳")
        if batch.errors:
            raise HasErrors(f"有 {len(batch.errors)} 筆錯誤，請修正後重新上傳", batch.errors[:20])

        if batch.kind == "invoice_summary":
            return self._commit_summary(batch, actor)
        superseded = []
        for old in self.ops.committed_batches(batch.kind, batch.company_id, batch.period_key):
            old_docs, _ = self.docs.list(company_ids={batch.company_id}, page_size=10000)
            mine = [d for d in old_docs if d.import_batch_id == old.id]
            paid = [d for d in mine if d.settled_amount]
            if paid:
                raise HasErrors(f"舊批次有 {len(paid)} 筆已付款，不能整批取代；請改用個別修改",
                                [{"row": 0, "message": str(d.id)} for d in paid[:20]])
            for d in mine:
                self.posting.void(d.id, f"由批次 {batch.id} 取代", actor)
            old.status, old.superseded_by = "superseded", batch.id
            superseded.append(str(old.id))
        if batch.kind == "payroll":
            # 薪資的來源鍵是「公司＋門市＋月份」：同月重新匯入時，作廢舊的那張
            for p in batch.parsed:
                prev = self.docs.get_by_source("payroll", p["input"]["source_ref"])
                if prev is not None and prev.import_batch_id != batch.id:
                    self.posting.void(prev.id, f"由批次 {batch.id} 取代", actor)

        created, drafts = 0, 0
        for p in batch.parsed:
            inp = _input_from_json(p["input"])
            doc = self.posting.create(inp, actor)
            created += 1
            if doc.status == "draft":
                drafts += 1
                self.ops.open_review("unclassified", doc.company_id, f"document:{doc.id}",
                                     {"document_id": str(doc.id), "counterparty": p.get("vendor"),
                                      "suggestion": p.get("suggestion")}, period_key=batch.period_key)
        batch.status, batch.committed_at = "committed", datetime.now(timezone.utc)
        return {"batch_id": str(batch.id), "created": created, "drafts_for_review": drafts,
                "total_amount": batch.total_amount, "superseded_batches": superseded}

    def _commit_summary(self, batch: ImportBatch, actor: str) -> dict[str, Any]:
        created = replaced = skipped = drafts = 0
        for p in batch.parsed:
            inp = _input_from_json(p["input"])
            existing = self.docs.get_by_source("invoice_summary", inp.source_ref)  # 入帳當下再比一次
            if existing is not None:
                if existing.settle_amount == inp.settle_amount:
                    skipped += 1
                    continue
                if existing.settled_amount:
                    raise HasErrors("有列先前已匯入且已付款，金額卻不同，不能取代",
                                    [{"row": p["row"], "message": inp.source_ref}])
                self.posting.void(existing.id, f"由批次 {batch.id} 取代（金額 {existing.settle_amount:,} → "
                                               f"{inp.settle_amount:,}）", actor)
                replaced += 1
            if inp.status == "draft" and inp.counterparty_id:  # 預覽之後才設定廠商類別的，入帳時再分類一次
                cp = self.catalog.counterparty(inp.counterparty_id)
                c = classify(self.catalog, cp, None, "expense", inp.settle_amount or 0, use_ai=False)
                if c.decided:
                    for ln in inp.lines:
                        ln.category_code = c.category_code
                    inp.status = "posted"
            doc = self.posting.create(inp, actor)
            created += 1
            if doc.status == "draft":
                drafts += 1
                self.ops.open_review("unclassified", doc.company_id, f"document:{doc.id}",
                                     {"document_id": str(doc.id), "counterparty": p.get("vendor"),
                                      "suggestion": p.get("suggestion")}, period_key=inp.lines[0].period_key)
        batch.status, batch.committed_at = "committed", datetime.now(timezone.utc)
        return {"batch_id": str(batch.id), "created": created, "drafts_for_review": drafts, "replaced": replaced,
                "skipped": skipped, "total_amount": batch.total_amount, "superseded_batches": []}

    def serialize(self, b: ImportBatch) -> dict[str, Any]:
        return {"id": str(b.id), "kind": b.kind, "company_id": b.company_id, "period": b.period_key,
                "file_name": b.file_name, "status": b.status, "row_count": b.row_count,
                "total_amount": b.total_amount, "warnings": b.warnings, "errors": b.errors,
                "preview": [{"row": p["row"], "counterparty": p.get("vendor"), "action": p.get("action", "create"),
                             "company_id": p["input"]["company_id"],
                             "settle_amount": p["input"]["settle_amount"], "lines": p["input"]["lines"],
                             "status": p["input"].get("status")} for p in b.parsed[:1000]]}


_STORE_SUFFIXES = ("分店", "門市", "店")


def store_core(name: str) -> str:
    """去掉結尾的「分店／門市／店」與空白、全形括號註記，用來比對不同寫法的同一家門市。"""
    n = re.sub(r"[\s　]|［.*?］|\[.*?\]|（.*?）|\(.*?\)", "", name or "")
    for suf in _STORE_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            return n[: -len(suf)]
    return n
