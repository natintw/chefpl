"""待確認的銀行交易：會計手動挑單據核對（金額可以有些許差距、對方帳號可以更正）。

差額怎麼處理（A＝銀行金額，T＝選取單據的未結清合計）：
  A ＝ T：全部結清。
  A ＞ T（多付／多收）：多出的金額另建一張已結清單據，類別預設：支出 E12 銀行手續費、收入 N03 雜項收入。
  A ＜ T（少付／少收）：
    leave_open：照單據月份由舊到新結清 A，剩下的維持未付／未收（之後再付）。
    book：單據全部結清，差額記一張單據，類別預設：支出（廠商折讓、抹零）N03 雜項收入；
          收入（刷卡、平台手續費被先扣）E11 刷卡手續費。
差額單據與補足的結清都記在「adj:<交易 ID>」名下；現金流系統刪除這筆交易時會一起撤回。

對方帳號：支出時，選的單據都是同一家廠商，就記住這個對方帳號（下次自動認出），
並從其他廠商身上拿掉同一個帳號（原本記錯的會被更正）。
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import any_ as sa_any, select
from sqlalchemy.orm import Session

from app.errors import ValidationFailed
from app.feature.common import DocumentInput, LineInput
from app.feature.models import Counterparty, Document, Settlement
from app.feature.posting import PostingService
from app.seams.company import get_directory

DEFAULT_EXTRA = {"out": "E12", "in": "N03"}       # 多付／多收
DEFAULT_SHORT = {"out": "N03", "in": "E11"}       # 少付（折讓）／少收（手續費被先扣）
DOC_TYPES = {"out": ("payable",), "in": ("receivable", "advance_receipt")}


def adj_ref(txn: str) -> str:
    return f"adj:{txn}"


class ManualMatchService:
    def __init__(self, session: Session):
        self.s = session
        self.posting = PostingService(session)
        self.docs, self.catalog = self.posting.docs, self.posting.catalog
        self.dir = get_directory()

    def match(self, it: dict[str, Any], payload: dict[str, Any], actor: str) -> dict[str, Any]:
        txn, amount, direction = it["cash_txn_id"], int(it["amount"]), it["direction"]
        txn_date = date.fromisoformat(str(it["txn_date"]))
        ids = list(dict.fromkeys(payload.get("document_ids") or []))
        if not ids:
            raise ValidationFailed("請勾選要核對的單據")
        docs: list[Document] = []
        for raw in ids:
            doc = self.docs.get(uuid.UUID(str(raw)), lock=True)
            if doc is None or doc.status == "void":
                raise ValidationFailed("有單據不存在或已作廢，請重新整理")
            if doc.doc_type not in DOC_TYPES[direction]:
                raise ValidationFailed("支出只能核對應付；收入只能核對應收或預收")
            if not self.dir.same_legal_entity(doc.company_id, it["company_id"]):
                raise ValidationFailed("單據與銀行帳戶不屬於同一家公司（含分公司）")
            if self._open(doc) <= 0:
                raise ValidationFailed(f"{self._name(doc)} 的單據已經結清")
            docs.append(doc)
        docs.sort(key=lambda d: (min((l.period_key for l in d.lines), default=""), d.doc_date, d.created_at))
        total = sum(self._open(d) for d in docs)
        diff = amount - total
        mode = payload.get("difference_mode") or ("book" if diff > 0 else "leave_open")
        cat_code = payload.get("difference_category") or (DEFAULT_EXTRA if diff > 0 else DEFAULT_SHORT)[direction]
        if diff > 0 and mode != "book":
            raise ValidationFailed(f"銀行金額比單據多 {diff:,}，多出的部分要選一個類別記帳")
        note = f"手動核對（{actor}）"

        settled, adj_doc = [], None
        if diff >= 0:
            for d in docs:
                self._settle(d, txn, it["cash_account_id"], txn_date, self._open(d), amount, note)
                settled.append(d)
            if diff > 0:
                adj_doc = self._diff_document(it, diff, cat_code, txn_date, actor, settle_with=(txn, it["cash_account_id"], amount))
        elif mode == "leave_open":
            left = amount
            for d in docs:
                part = min(left, self._open(d))
                if part <= 0:
                    break
                self._settle(d, txn, it["cash_account_id"], txn_date, part, amount, note)
                settled.append(d)
                left -= part
        elif mode == "book":
            short = -diff
            for d in docs:
                self._settle(d, txn, it["cash_account_id"], txn_date, self._open(d), amount, note)
                settled.append(d)
            # 補足：最後一張單據多結清的部分記在 adj 名下（銀行只付了 A）
            self._rebalance(docs, txn, short, txn_date, note)
            adj_doc = self._diff_document(it, short, cat_code, txn_date, actor, settle_with=(adj_ref(txn), "adjust", None))
        else:
            raise ValidationFailed("差額處理方式只能是 leave_open（留著未付）或 book（記一張單據）")

        remembered = self._remember(it, docs) if payload.get("remember_account", True) else None
        for d in docs:
            self.s.refresh(d)
            self.posting.recompute_settlement(d)
        return {"settled_documents": [str(d.id) for d in settled], "difference": diff, "difference_mode": mode if diff else None,
                "difference_document": str(adj_doc.id) if adj_doc else None, "remembered_account": remembered}

    # ------------------------------------------------------------------
    @staticmethod
    def _open(d: Document) -> int:
        return (d.settle_amount or 0) - sum(st.amount for st in d.settlements if st.reversed_at is None)

    @staticmethod
    def _name(d: Document) -> str:
        return d.counterparty.name if d.counterparty else "（未指定對象）"

    def _settle(self, d: Document, txn: str, account: str, txn_date: date, amt: int, bank_amount: int | None, note: str):
        self.docs.add_settlement(Settlement(document_id=d.id, cash_txn_id=txn, cash_account_id=account, txn_date=txn_date,
                                            amount=amt, bank_amount=bank_amount, match_level="manual", note=note))
        self.s.refresh(d)

    def _rebalance(self, docs: list[Document], txn: str, short: int, txn_date: date, note: str) -> None:
        """少付記帳：銀行那筆只能分出 A，把最後幾張單據的結清改成「銀行 A ＋ 補足 short」。"""
        left = short
        for d in reversed(docs):
            if left <= 0:
                break
            st = next(s for s in d.settlements if s.cash_txn_id == txn and s.reversed_at is None)
            take = min(left, st.amount)
            st.amount -= take
            if st.amount == 0:
                self.s.delete(st)
            self.s.flush()
            self.docs.add_settlement(Settlement(document_id=d.id, cash_txn_id=adj_ref(txn), cash_account_id="adjust",
                                                txn_date=txn_date, amount=take, bank_amount=None, match_level="manual",
                                                note=f"{note}：差額"))
            self.s.refresh(d)
            left -= take

    def _diff_document(self, it: dict, amount: int, cat_code: str, txn_date: date, actor: str, *, settle_with) -> Document:
        cat = self.catalog.category(cat_code)
        if cat is None or cat.nature not in ("expense", "income"):
            raise ValidationFailed(f"差額類別 {cat_code} 不存在或不是損益類別")
        doc_type = "payable" if cat.nature == "expense" else "receivable"
        doc = self.posting.create(DocumentInput(
            doc_type=doc_type, company_id=it["company_id"], doc_date=txn_date, source_kind="bank_only",
            source_ref=adj_ref(it["cash_txn_id"]), counterparty_name=None, settle_amount=amount, tax_amount=0,
            memo=f"銀行核對差額（{it.get('bank_memo') or ''} {txn_date.isoformat()}）",
            lines=[LineInput(str(txn_date)[:7], cat_code, amount, None, "銀行核對差額")]), actor)
        txn, account, bank_amount = settle_with
        self.docs.add_settlement(Settlement(document_id=doc.id, cash_txn_id=txn, cash_account_id=account, txn_date=txn_date,
                                            amount=amount, bank_amount=bank_amount, match_level="manual", note="銀行核對差額"))
        self.s.refresh(doc)
        self.posting.recompute_settlement(doc)
        return doc

    def _remember(self, it: dict, docs: list[Document]) -> str | None:
        from app.feature.bank_matching import _acct_key
        key = _acct_key(it.get("counterparty_account_no"))
        cps = {d.counterparty_id for d in docs}
        if it["direction"] != "out" or not key or len(cps) != 1 or None in cps:
            return None
        cp = self.s.get(Counterparty, next(iter(cps)))
        for other in self.s.scalars(select(Counterparty).where(sa_any(Counterparty.bank_accounts) == key, Counterparty.id != cp.id)):
            other.bank_accounts = [a for a in other.bank_accounts if a != key]   # 原本記錯的拿掉
        if key not in (cp.bank_accounts or []):
            cp.bank_accounts = [*(cp.bank_accounts or []), key]
        return f"{cp.name}：帳號末 5 碼 {key}"
