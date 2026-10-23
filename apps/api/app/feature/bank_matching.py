"""銀行交易 webhook 的比對（現金流系統只送銀行交易，比對在帳務系統這邊做）。

為什麼在這邊比對：付廠商的錢通常是「一家公司一次匯一筆」，但應付是「一家店一張」。
只有帳務系統知道每家店各欠多少，才能把一筆匯款拆回各店的單據。

支出（direction=out）的比對順序（Bill 指定）：
  1. 對方帳號：以前對到過的帳號，直接認得是哪家廠商
  2. 摘要／對方戶名：廠商名稱、別名、付款關鍵字出現在摘要或戶名裡
  3. 備註
認出廠商後，在「同一法人」的未結清應付裡找金額剛好相符的組合：
  全部未結清合計 → 依月份累加 → 任意組合（只接受唯一解）。
對到就把每張單據標成已付（同一個 cash_txn_id、bank_amount＝匯款金額）；名稱比對成功時記住對方帳號。
認不出廠商、或金額湊不出唯一組合 → 進「待確認」（銀行有、單據無），能猜到的組合放在建議裡讓會計一鍵確認。

收入（direction=in）：同一法人的未結清應收裡，金額完全相同且只有一張 → 標成已收；否則進待確認。
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.feature.models import Counterparty, Document
from app.feature.settlement import SettlementService
from app.seams.company import get_directory

DUE_SLACK_DAYS = 20          # 到期日在交易日 20 天內的單據才拿來比（提早付款）
MAX_SUBSET_DOCS = 20         # 任意組合最多試 20 張（2^20 ≈ 一百萬種，實務上一家廠商一個月不會超過）
_SUFFIX = re.compile(r"(股份有限公司|有限公司|企業社|企業行|商行|實業|企業|公司|行)$")
_BRANCH = re.compile(r"[\(（].*?[\)）]|(高雄|台北|臺北|台中|臺中|台南|臺南|南區|北區|中區).{0,4}(分公司|營業所|門市部|營業處)$")


def core_name(name: str | None) -> str:
    """比對用的名稱核心：去空白、括號註記、分公司營業所、公司型態字尾。"""
    n = re.sub(r"[\s　]", "", name or "")
    n = _BRANCH.sub("", n)
    prev = None
    while prev != n:
        prev, n = n, _SUFFIX.sub("", n)
    return n


def _acct_key(v: str | None) -> str | None:
    """帳號只留數字，取末 5 碼比對（現金流系統送來的通常是遮蔽過的帳號）。"""
    digits = re.sub(r"\D", "", v or "")
    return digits[-5:] if len(digits) >= 5 else None


class BankMatchService:
    def __init__(self, session: Session):
        self.s = session
        self.dir = get_directory()
        self.settle = SettlementService(session)
        self.docs, self.ops = self.settle.docs, self.settle.ops

    # ------------------------------------------------------------------ 入口
    def receive(self, items: list[dict[str, Any]], actor: str) -> dict[str, Any]:
        self._vendors = [cp for cp in self.s.scalars(select(Counterparty).where(Counterparty.is_active.is_(True)))]
        results, counts = [], {"matched": 0, "review": 0, "duplicate": 0, "rejected": 0}
        for it in items:
            r = self._one(it, actor)
            counts[r["status"]] += 1
            results.append(r)
        self.s.flush()
        return {**counts, "results": results}

    def _one(self, it: dict[str, Any], actor: str) -> dict[str, Any]:
        txn = it["cash_txn_id"]
        base = {"cash_txn_id": txn}
        if self.docs.txn_already_used(txn) or self.ops.review_exists("bank_only", f"cash_txn:{txn}"):
            return {**base, "status": "duplicate"}
        acct = self.dir.get_bank_account(it["cash_account_id"])
        if acct is None:
            return {**base, "status": "rejected", "message": f"不認得銀行帳戶 {it['cash_account_id']}"}
        company_ids = {c.id for c in self.dir.list_companies() if self.dir.same_legal_entity(c.id, acct.company_id)}
        txn_date = date.fromisoformat(str(it["txn_date"]))
        amount = int(it["amount"])

        if it["direction"] == "in":
            docs = self._open_docs(company_ids, "receivable", None, txn_date)
            hits = [d for d in docs if _outstanding(d) == amount]
            if len(hits) == 1:
                return self._apply(it, acct.company_id, hits, None, "amount", actor)
            return self._review(it, acct.company_id, None, hits[:5] if len(hits) > 1 else [],
                                "金額相同的應收不只一張" if len(hits) > 1 else "找不到對應的應收")

        vendors, how = self._identify_vendor(it)
        tried = []
        for cp in vendors:
            combo = self._combo(self._open_docs(company_ids, "payable", cp.id, txn_date), amount)
            tried.append((cp, combo))
        exact = [(cp, c) for cp, c in tried if c and c != "ambiguous"]
        if len(exact) == 1:
            cp, combo = exact[0]
            if how != "account":
                self._remember_account(cp, it.get("counterparty_account_no"))
            return self._apply(it, acct.company_id, combo, cp, how, actor)

        # 認出廠商但金額湊不起來／不只一種湊法，或根本認不出廠商：進待確認，附上能猜到的
        if not vendors:
            guess = self._guess_by_amount(company_ids, amount, txn_date)
            reason = "認不出廠商" + (f"；金額剛好等於「{guess[0].name}」未付合計" if guess else "")
            return self._review(it, acct.company_id, guess[0] if guess else None, guess[1] if guess else [], reason)
        cp, combo = tried[0]
        reason = (f"「{cp.name}」有多種單據組合金額相同，請確認" if combo == "ambiguous"
                  else f"「{cp.name}」未付單據湊不出 {amount:,}")
        return self._review(it, acct.company_id, cp, [], reason)

    # ------------------------------------------------------------------ 認廠商
    def _identify_vendor(self, it: dict[str, Any]) -> tuple[list[Counterparty], str]:
        key = _acct_key(it.get("counterparty_account_no"))
        if key:
            hits = [cp for cp in self._vendors if key in (cp.bank_accounts or [])]
            if hits:
                return hits, "account"
        for field, how in (("counterparty_name", "name"), ("bank_memo", "memo"), ("bank_note", "note")):
            text = re.sub(r"[\s　]", "", it.get(field) or "")
            if not text:
                continue
            hits = [cp for cp in self._vendors if self._name_hit(cp, text)]
            if hits:
                return hits, how
        return [], "none"

    @staticmethod
    def _name_hit(cp: Counterparty, text: str) -> bool:
        tcore = core_name(text)
        for n in [cp.name, *(cp.aliases or [])]:
            c = core_name(n)
            if len(c) < 2:
                continue
            if c in text or (len(tcore) >= 4 and c.startswith(tcore[:4])) or (len(c) >= 4 and c[:4] in text):
                return True
        return any(k and len(k) >= 2 and k in text for k in (cp.keywords or []))

    def _remember_account(self, cp: Counterparty, account: str | None) -> None:
        key = _acct_key(account)
        if key and key not in (cp.bank_accounts or []):
            cp.bank_accounts = [*(cp.bank_accounts or []), key]

    # ------------------------------------------------------------------ 找單據組合
    def _open_docs(self, company_ids: set[str], doc_type: str, cp_id: uuid.UUID | None, txn_date: date) -> list[Document]:
        stmt = select(Document).where(
            Document.doc_type == doc_type, Document.status.in_(("posted", "draft")),
            Document.settle_status.in_(("open", "partial")), Document.company_id.in_(company_ids))
        if cp_id is not None:
            stmt = stmt.where(Document.counterparty_id == cp_id)
        latest = txn_date + timedelta(days=DUE_SLACK_DAYS)
        docs = [d for d in self.s.scalars(stmt) if _outstanding(d) > 0 and (d.due_date is None or d.due_date <= latest)]
        return sorted(docs, key=lambda d: (_period(d), d.due_date or date.max, str(d.id)))

    @staticmethod
    def _combo(docs: list[Document], amount: int) -> list[Document] | str | None:
        """金額剛好相符的單據組合；唯一解才回傳，不只一種回傳 "ambiguous"，沒有回傳 None。"""
        if not docs:
            return None
        if sum(_outstanding(d) for d in docs) == amount:
            return docs
        running, chosen = 0, []
        for d in docs:  # 依月份累加（先付舊的）
            running += _outstanding(d)
            chosen.append(d)
            if running == amount:
                return list(chosen)
            if running > amount:
                break
        if len(docs) > MAX_SUBSET_DOCS:
            return None
        found = None
        for r in range(1, len(docs) + 1):
            for combo in itertools.combinations(docs, r):
                if sum(_outstanding(d) for d in combo) == amount:
                    if found is not None:
                        return "ambiguous"
                    found = list(combo)
        return found

    def _guess_by_amount(self, company_ids: set[str], amount: int, txn_date: date):
        """認不出廠商時：哪一家廠商的未付合計剛好等於金額（只在唯一時給建議）。"""
        by_cp: dict[uuid.UUID, list[Document]] = {}
        for d in self._open_docs(company_ids, "payable", None, txn_date):
            if d.counterparty_id:
                by_cp.setdefault(d.counterparty_id, []).append(d)
        hits = [(cid, ds) for cid, ds in by_cp.items() if sum(_outstanding(d) for d in ds) == amount]
        if len(hits) != 1:
            return None
        cp = self.s.get(Counterparty, hits[0][0])
        return cp, hits[0][1]

    # ------------------------------------------------------------------ 結果
    def _apply(self, it: dict[str, Any], company_id: str, docs: list[Document], cp: Counterparty | None,
               how: str, actor: str) -> dict[str, Any]:
        amount = int(it["amount"])
        level = "exact" if len(docs) == 1 else "partial"  # partial：一筆匯款分到多張單據
        items = [{"document_id": str(d.id), "cash_txn_id": it["cash_txn_id"], "cash_account_id": it["cash_account_id"],
                  "txn_date": str(it["txn_date"]), "amount": _outstanding(d), "bank_amount": amount,
                  "match_level": level, "matched_by": "auto"} for d in docs]
        res = self.settle.apply(items, actor)
        if res["rejected"]:
            return self._review(it, company_id, cp, docs, "；".join(r["message"] for r in res["rejected"]))
        return {"cash_txn_id": it["cash_txn_id"], "status": "matched", "matched_by": how,
                "counterparty": cp.name if cp else None, "documents": [str(d.id) for d in docs]}

    def _review(self, it: dict[str, Any], company_id: str, cp: Counterparty | None, docs: list[Document],
                reason: str) -> dict[str, Any]:
        suggestion = None
        if docs:
            suggestion = {"counterparty_id": str(cp.id) if cp else None, "counterparty_name": cp.name if cp else None,
                          "documents": [{"document_id": str(d.id), "amount": _outstanding(d),
                                         "store_ids": sorted({l.store_id for l in d.lines if l.store_id}),
                                         "period": _period(d)} for d in docs]}
        item = {k: (str(v) if isinstance(v, date) else v) for k, v in it.items()}
        item["company_id"] = company_id
        self.ops.open_review("bank_only", company_id, f"cash_txn:{it['cash_txn_id']}",
                             {"item": item, "reason": reason, "suggestion": suggestion,
                              "counterparty_hint": cp.name if cp else None},
                             period_key=str(it["txn_date"])[:7])
        return {"cash_txn_id": it["cash_txn_id"], "status": "review", "reason": reason,
                "suggested_documents": [str(d.id) for d in docs]}


def _outstanding(d: Document) -> int:
    return (d.settle_amount or 0) - (d.settled_amount or 0)


def _period(d: Document) -> str:
    return min((l.period_key for l in d.lines), default="")

