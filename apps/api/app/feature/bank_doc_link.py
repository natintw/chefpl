"""把「銀行建立的單據」和 POS（或發票）單據連在一起。

常見情況：門市 9/23 的現金營收（POS 匯入成應收「門市現金（存入銀行）」），店長 9/24 存進銀行，
銀行備註寫「9/23」。銀行那筆先被建成一張「來源：銀行」的應收，POS 匯入後同一筆錢算了兩次。
這裡把兩邊連起來：銀行那筆改對到 POS 應收（銀行建立的那張作廢，POS 那張變已收、入款日＝銀行日）。

兩種用法：
  link(ids)：清單上勾選的單據，銀行那幾張 ↔ 其他幾張，依金額配對（一對一優先；只剩一筆銀行時，其餘全部對給它）。
  auto(...)：自動找配對。銀行備註／對象寫了日期（9/23、9/15+9/16）就找那幾天的 POS 單據、金額加總相等；
            沒寫日期就找銀行日前 7 天內金額完全相同、而且只有一張的。只列出有把握的，會計勾選後才套用。
都先回傳計畫（dry_run），確認後才寫入；每一組各自成功或失敗，互不影響。
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import logging

from app.errors import AppError, ValidationFailed
from app.feature.manual_match import UnmatchService
from app.feature.models import Document, ReviewItem
from app.seams.company import get_directory

log = logging.getLogger("ledger.bank_doc_link")

FAMILY = {"receivable": ("receivable", "advance_receipt"), "advance_receipt": ("receivable", "advance_receipt"),
          "payable": ("payable",)}
DATE_HINT = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s*[/／.月]\s*(3[01]|[12]\d|0?[1-9])(?!\d)")
NEAR_DAYS = 7      # 沒寫日期時，往前找幾天
HINT_SPAN = 45     # 備註日期最多比銀行日早幾天


def _open(d: Document) -> int:
    return (d.settle_amount or 0) - sum(st.amount for st in d.settlements if st.reversed_at is None)


class BankDocLinkService:
    def __init__(self, session: Session):
        self.s = session
        self.um = UnmatchService(session)
        self.dir = get_directory()

    # ------------------------------------------------------------------ 共用
    def _bank_info(self, d: Document) -> dict[str, Any] | None:
        txns = self.um.txns_of(d)
        if len(txns) != 1:
            return None
        txn = txns[0]
        sts = [st for st in d.settlements if st.cash_txn_id == txn and st.reversed_at is None]
        amount = next((st.bank_amount for st in sts if st.bank_amount), None) or sum(st.amount for st in sts) or (d.settle_amount or 0)
        txn_date = sts[0].txn_date if sts else d.doc_date
        return {"txn": txn, "amount": int(amount), "date": txn_date}

    def _hints(self, d: Document, txn: str, bank_date: date) -> list[date]:
        texts = [d.memo or "", d.counterparty.name if d.counterparty else ""]
        rv = self.s.scalar(select(ReviewItem).where(ReviewItem.kind == "bank_only", ReviewItem.ref == f"cash_txn:{txn}")
                           .order_by(ReviewItem.created_at.desc()).limit(1))
        if rv is not None:
            it = (rv.payload or {}).get("item") or {}
            texts += [str(it.get("bank_note") or ""), str(it.get("counterparty_name") or "")]
        out = []
        for m, dd in DATE_HINT.findall(" ".join(texts)):
            for y in (bank_date.year, bank_date.year - 1):
                try:
                    c = date(y, int(m), int(dd))
                except ValueError:
                    continue
                if timedelta(0) <= bank_date - c <= timedelta(days=HINT_SPAN):
                    out.append(c)
                    break
        return sorted(set(out))

    @staticmethod
    def _card(d: Document) -> dict[str, Any]:
        return {"id": str(d.id), "name": d.counterparty.name if d.counterparty else (d.memo or "（未指定）"),
                "date": d.doc_date.isoformat(), "amount": _open(d), "source_kind": d.source_kind,
                "store_ids": sorted({l.store_id for l in d.lines if l.store_id})}

    def _group(self, b: Document, info: dict, docs: list[Document], how: str) -> dict[str, Any]:
        total = sum(_open(x) for x in docs)
        return {"bank_document": {**self._card(b), "amount": info["amount"], "date": info["date"].isoformat(), "txn": info["txn"]},
                "documents": [self._card(x) for x in sorted(docs, key=lambda x: x.doc_date)],
                "total": total, "difference": info["amount"] - total, "how": how}

    @staticmethod
    def _subset(pool: list[Document], amount: int, max_n: int = 4) -> list[Document] | None:
        """金額加總剛好等於 amount 的組合（只接受唯一解）。"""
        pool = pool[:14]
        hits = []
        for n in range(1, min(max_n, len(pool)) + 1):
            for combo in itertools.combinations(pool, n):
                if sum(_open(x) for x in combo) == amount:
                    hits.append(list(combo))
                    if len(hits) > 1:
                        return None
            if hits:
                return hits[0]
        return None

    # ------------------------------------------------------------------ 勾選的配對
    def plan_selected(self, ids: list[uuid.UUID]) -> dict[str, Any]:
        docs = [d for d in (self.um.docs.get(i) for i in dict.fromkeys(ids)) if d is not None and d.status != "void"]
        banks = [(d, self._bank_info(d)) for d in docs if self.um.txns_of(d)]
        if any(info is None for _, info in banks):
            raise ValidationFailed("有勾選的單據對到不只一筆銀行交易，請個別處理")
        bank_ids = {d.id for d, _ in banks}
        others = [d for d in docs if d.id not in bank_ids]
        if not banks:
            raise ValidationFailed("勾選裡沒有「銀行」來源或由銀行結清的單據")
        if not others:
            raise ValidationFailed("請同時勾選要對應的 POS／發票單據（未收或未付的）")
        for d in others:
            if _open(d) <= 0:
                raise ValidationFailed(f"「{self._card(d)['name']} {d.doc_date}」已經結清，不能再對應")
        fam = {FAMILY.get(d.doc_type, (d.doc_type,)) for d in docs}
        if len(fam) != 1:
            raise ValidationFailed("應收和應付不能混在一起對應")
        ref = docs[0].company_id
        if any(not self.dir.same_legal_entity(ref, d.company_id) for d in docs):
            raise ValidationFailed("勾選的單據不屬於同一家公司（含分公司）")

        groups, left = [], list(others)
        pending = sorted(banks, key=lambda x: x[1]["date"])
        rest = []
        for b, info in pending:          # 一對一：金額相同、日期最接近（POS 日不晚於銀行日優先）
            cands = [x for x in left if _open(x) == info["amount"]]
            if not cands:
                rest.append((b, info))
                continue
            pick = min(cands, key=lambda x: (x.doc_date > info["date"], abs((info["date"] - x.doc_date).days)))
            left.remove(pick)
            groups.append(self._group(b, info, [pick], "same_amount"))
        unmatched_bank = []
        for b, info in rest:             # 一筆銀行對多張（例：9/15＋9/16 一起存）
            combo = self._subset(sorted(left, key=lambda x: x.doc_date), info["amount"])
            if combo:
                for x in combo:
                    left.remove(x)
                groups.append(self._group(b, info, combo, "sum"))
            else:
                unmatched_bank.append((b, info))
        if len(unmatched_bank) == 1 and left:   # 只剩一筆銀行：剩下的全部對給它，差額由會計決定
            b, info = unmatched_bank.pop()
            groups.append(self._group(b, info, left, "rest"))
            left = []
        return {"groups": groups,
                "unmatched_bank": [{**self._card(b), "amount": info["amount"]} for b, info in unmatched_bank],
                "unmatched_documents": [self._card(x) for x in left]}

    # ------------------------------------------------------------------ 自動找配對
    def plan_auto(self, company_ids: set[str] | None, store_id: str | None, period: str | None,
                  doc_type: str = "receivable") -> dict[str, Any]:
        fam = FAMILY.get(doc_type, (doc_type,))
        stmt = select(Document).where(Document.source_kind == "bank_only", Document.status != "void",
                                      Document.doc_type.in_(fam))
        if company_ids is not None:
            stmt = stmt.where(Document.company_id.in_(company_ids))
        banks = []
        for d in self.s.scalars(stmt.order_by(Document.doc_date)):
            if period and d.doc_date.isoformat()[:7] != period:
                continue
            if store_id and store_id not in {l.store_id for l in d.lines}:
                continue
            if d.source_ref and d.source_ref.startswith("adj:"):
                continue      # 核對差額單不是入款
            info = self._bank_info(d)
            if info and (d.settle_amount or 0) > 0:
                banks.append((d, info))
        if not banks:
            return {"groups": [], "skipped": 0}
        lo = min(i["date"] for _, i in banks) - timedelta(days=HINT_SPAN)
        hi = max(i["date"] for _, i in banks)
        cstmt = select(Document).where(Document.source_kind != "bank_only", Document.status == "posted",
                                       Document.doc_type.in_(fam), Document.settle_status.in_(("open", "partial")),
                                       Document.doc_date >= lo, Document.doc_date <= hi)
        cands = [c for c in self.s.scalars(cstmt) if _open(c) > 0]
        used: set[uuid.UUID] = set()
        groups, skipped = [], 0
        for b, info in banks:
            stores = {l.store_id for l in b.lines if l.store_id}
            pool = [c for c in cands if c.id not in used and self.dir.same_legal_entity(c.company_id, b.company_id)
                    and (not stores or not {l.store_id for l in c.lines if l.store_id} or
                         stores & {l.store_id for l in c.lines if l.store_id})]
            hints = self._hints(b, info["txn"], info["date"])
            pick, how = None, None
            if hints:
                on_days = [c for c in pool if c.doc_date in hints]
                by_cp: dict[Any, list[Document]] = {}
                for c in on_days:
                    by_cp.setdefault(c.counterparty_id, []).append(c)
                for grp in sorted(by_cp.values(), key=len):      # 先找同一種收款方式（例：門市現金）
                    pick = self._subset(sorted(grp, key=lambda x: x.doc_date), info["amount"])
                    if pick:
                        break
                if not pick:
                    pick = self._subset(sorted(on_days, key=lambda x: x.doc_date), info["amount"])
                how = "note_date" if pick else None
            if not pick:
                near = [c for c in pool if timedelta(0) <= info["date"] - c.doc_date <= timedelta(days=NEAR_DAYS)
                        and _open(c) == info["amount"]]
                if len(near) == 1:
                    pick, how = near, "same_amount_near"
            if pick:
                used.update(c.id for c in pick)
                g = self._group(b, info, pick, how)
                g["hint_dates"] = [h.isoformat() for h in hints]
                groups.append(g)
            else:
                skipped += 1
        return {"groups": groups, "skipped": skipped}

    # ------------------------------------------------------------------ 套用
    def apply(self, groups: list[dict[str, Any]], actor: str) -> dict[str, Any]:
        done, failed = [], []
        for g in groups:
            bank_id = uuid.UUID(str(g["bank_document_id"]))
            payload = {"document_ids": [str(x) for x in g["document_ids"]], "remember_account": False}
            if g.get("difference_mode"):
                payload["difference_mode"] = g["difference_mode"]
            if g.get("difference_category"):
                payload["difference_category"] = g["difference_category"]
            sp = self.s.begin_nested()
            try:
                r = self.um.relink(bank_id, payload, actor)
                sp.commit()
                done.append({"bank_document_id": str(bank_id), "difference": r.get("difference", 0)})
            except AppError as e:
                sp.rollback()
                failed.append({"bank_document_id": str(bank_id), "message": e.message})
            except Exception as e:  # noqa: BLE001 — 一組出錯不影響其他組；寫 log 方便查
                sp.rollback()
                log.exception("link-apply 失敗：bank_document=%s", bank_id)
                failed.append({"bank_document_id": str(bank_id),
                               "message": f"系統錯誤（{type(e).__name__}）：{str(e).splitlines()[0][:160]}"})
        return {"linked": len(done), "results": done, "failed": failed}
