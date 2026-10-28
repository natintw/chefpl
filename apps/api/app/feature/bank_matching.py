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

收入（direction=in）：同一法人的未結清應收與預收裡，金額完全相同且只有一張 → 標成已收；否則進待確認。

刪除（event=bank_transactions.deleted）：現金流系統把匯錯的交易刪掉時呼叫。撤回這筆交易造成的一切：
  付款配對取消（單據回到未付）、由這筆交易建立的單據作廢（已關帳的月份不動，改開待確認）、還沒處理的待確認撤下。
  之後同一筆交易重新匯入，會當成新的再比對一次。
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import date, timedelta
from typing import Any

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.feature.bank_accounts import upsert_from_items
from app.feature.common import outstanding
from app.feature.models import Counterparty, Document, ReviewItem, Settlement
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
        # 只用廠商／客戶比對；從銀行交易建立的對象（員工薪資、銀行）不算廠商
        self._vendors = [cp for cp in self.s.scalars(select(Counterparty).where(Counterparty.is_active.is_(True)))
                         if set(cp.roles or []) - {"payee"}]
        results, counts = [], {"matched": 0, "review": 0, "duplicate": 0, "rejected": 0, "skipped": 0}
        bad_accounts = upsert_from_items(self.s, items)  # 帳戶資料以現金流系統為準
        from app.feature.cash_category_map import CashCategoryMapService
        CashCategoryMapService(self.s).register(items)  # 現金流新分類加進對照表
        for it in items:
            if it["cash_account_id"] in bad_accounts:
                r = {"cash_txn_id": it["cash_txn_id"], "status": "rejected", "message": bad_accounts[it["cash_account_id"]]}
            else:
                r = self._one(it, actor)
            counts[r["status"]] += 1
            results.append(r)
        self.s.flush()
        return {**counts, "results": results}

    def _one(self, it: dict[str, Any], actor: str) -> dict[str, Any]:
        txn = it["cash_txn_id"]
        base = {"cash_txn_id": txn}
        if self.docs.txn_already_used(txn):
            return {**base, "status": "duplicate"}
        if self._has_live_review(txn):
            # 先前進了待確認、現在現金流分類是「不需核對」（例：歸成薪資）→ 自動撤下
            if self._quiet(it):
                open_item = self.s.scalar(select(ReviewItem).where(ReviewItem.kind == "bank_only",
                                                                   ReviewItem.ref == f"cash_txn:{txn}", ReviewItem.status == "open"))
                if open_item is not None:
                    open_item.status, open_item.resolved_by = "dismissed", "system"
                    open_item.resolved_at = datetime.now(timezone.utc)
                    open_item.resolution = {"auto_skipped": True, "category": self._cat(it)}
                    return {**base, "status": "skipped", "reason": f"現金流分類：{self._cat(it)}（不需核對）"}
            self._refresh_category(txn, it)
            return {**base, "status": "duplicate"}
        acct = self.dir.get_bank_account(it["cash_account_id"])
        if acct is None:
            return {**base, "status": "rejected", "message": f"不認得銀行帳戶 {it['cash_account_id']}"}
        company_ids = {c.id for c in self.dir.list_companies() if self.dir.same_legal_entity(c.id, acct.company_id)}
        txn_date = date.fromisoformat(str(it["txn_date"]))
        amount = int(it["amount"])

        if it["direction"] == "in":
            docs = self._open_docs(company_ids, ("receivable", "advance_receipt", "allowance"), None, txn_date)
            hits = [d for d in docs if _outstanding(d) == amount]
            if len(hits) == 1:
                return self._apply(it, acct.company_id, hits, None, "amount", actor)
            return self._review(it, acct.company_id, None, hits[:5] if len(hits) > 1 else [],
                                "金額相同的應收／預收不只一張" if len(hits) > 1 else "找不到金額相同的應收或預收")

        vendors, how = self._identify_vendor(it)
        tried = []
        for cp in vendors:
            combo = self._combo(self._open_docs(company_ids, ("payable", "prepayment"), cp.id, txn_date), amount)
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

    def _has_live_review(self, txn: str) -> bool:
        """這筆交易已經有待確認（處理中或已處理）。因交易被刪而撤下的、或因分類自動略過的不算，重送時要再比一次。"""
        for it in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "bank_only",
                                                          ReviewItem.ref == f"cash_txn:{txn}")):
            r = it.resolution or {}
            if not (r.get("withdrawn") or r.get("auto_skipped")):
                return True
        return False

    def _refresh_category(self, txn: str, it: dict[str, Any]) -> None:
        """重送時更新待確認上的現金流分類（先前送的沒帶分類，或會計在現金流改了分類）。"""
        if not it.get("cash_category_code"):
            return
        row = self.s.scalar(select(ReviewItem).where(ReviewItem.kind == "bank_only", ReviewItem.ref == f"cash_txn:{txn}",
                                                     ReviewItem.status == "open"))
        if row is not None:
            item = dict((row.payload or {}).get("item") or {})
            item.update(cash_category_code=it.get("cash_category_code"), cash_category_name=it.get("cash_category_name"))
            row.payload = {**(row.payload or {}), "item": item}

    def _quiet(self, it: dict[str, Any]) -> bool:
        code = (it.get("cash_category_code") or "").strip()
        quiet = {c.strip() for c in get_settings().bank_quiet_categories.split(",") if c.strip()}
        return bool(code) and code in quiet

    @staticmethod
    def _cat(it: dict[str, Any]) -> str:
        return it.get("cash_category_name") or it.get("cash_category_code") or ""

    def _skip(self, it: dict[str, Any], company_id: str, reason: str) -> dict[str, Any]:
        """不需核對的分類：不進待確認，留一筆已略過的紀錄（狀態查詢顯示「不需核對」，分類改了重送會再比一次）。"""
        ref = f"cash_txn:{it['cash_txn_id']}"
        item = {k: (str(v) if isinstance(v, date) else v) for k, v in it.items()}
        item["company_id"] = company_id
        row = next((r for r in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "bank_only", ReviewItem.ref == ref))
                    if (r.resolution or {}).get("auto_skipped")), None)
        if row is None:
            row = ReviewItem(kind="bank_only", company_id=company_id, ref=ref, period_key=str(it["txn_date"])[:7], payload={})
            self.s.add(row)
        row.payload = {"item": item, "reason": reason}
        row.status, row.resolved_by, row.resolved_at = "dismissed", "system", datetime.now(timezone.utc)
        row.resolution = {"auto_skipped": True, "category": self._cat(it)}
        self.s.flush()
        return {"cash_txn_id": it["cash_txn_id"], "status": "skipped",
                "reason": f"現金流分類：{self._cat(it)}（不需核對）；{reason}"}

    # ------------------------------------------------------------------ 查詢核對狀態
    def status(self, txn_ids: list[str]) -> dict[str, Any]:
        """每筆交易在帳務系統的現況（現金流系統的「帳務核對」頁用）。

        matched：已結清單據（自動對上、或會計在待確認確認／建立單據）
        review：還在待確認
        dismissed：會計在待確認按了略過（例：零用金補錢、內部調撥）
        unknown：帳務系統沒有這筆（沒送過、被拒、或已撤回）
        """
        ids = list(dict.fromkeys(txn_ids))
        sts = self.s.scalars(select(Settlement).where(Settlement.cash_txn_id.in_(ids), Settlement.reversed_at.is_(None))).all()
        by_txn: dict[str, list[Settlement]] = {}
        for st in sts:
            by_txn.setdefault(st.cash_txn_id, []).append(st)
        reviews: dict[str, ReviewItem] = {}
        for it in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "bank_only",
                                                          ReviewItem.ref.in_([f"cash_txn:{i}" for i in ids]))):
            if (it.resolution or {}).get("withdrawn"):
                continue
            cur = reviews.get(it.ref)
            if cur is None or it.created_at > cur.created_at:
                reviews[it.ref] = it
        out, counts = [], {"matched": 0, "review": 0, "dismissed": 0, "skipped": 0, "unknown": 0}
        for txn in ids:
            r: dict[str, Any] = {"cash_txn_id": txn}
            if txn in by_txn:
                docs = [st.document for st in by_txn[txn]]
                names = sorted({d.counterparty.name for d in docs if d.counterparty})
                manual = any(st.match_level == "manual" for st in by_txn[txn])
                r.update(status="matched", how="manual" if manual else "auto",
                         summary=f"{'、'.join(names) or '單據'} {len(docs)} 張" + ("（人工確認）" if manual else ""),
                         documents=[str(d.id) for d in docs])
            elif (rv := reviews.get(f"cash_txn:{txn}")) is not None:
                if rv.status == "open":
                    r.update(status="review", summary=(rv.payload or {}).get("reason") or "待確認")
                elif rv.status == "dismissed" and (rv.resolution or {}).get("auto_skipped"):
                    r.update(status="skipped", summary=f"現金流分類：{(rv.resolution or {}).get('category') or ''}（不需核對）")
                elif rv.status == "dismissed":
                    r.update(status="dismissed", summary="在帳務系統略過")
                else:
                    r.update(status="matched", how="manual", summary="已在帳務系統處理")
            else:
                r.update(status="unknown", summary="帳務系統沒有這筆")
            counts[r["status"]] += 1
            out.append(r)
        return {**counts, "results": out}

    # ------------------------------------------------------------------ 刪除：撤回
    def withdraw(self, txn_ids: list[str], actor: str) -> dict[str, Any]:
        results, counts = [], {"withdrawn": 0, "not_found": 0, "needs_review": 0}
        now = datetime.now(timezone.utc)
        reason = "銀行交易已在現金流系統刪除"
        for txn in dict.fromkeys(txn_ids):  # 去重、保留順序
            touched, voided, dismissed, flagged = [], [], 0, []
            # 1) 付款配對取消 → 單據回到未付（不影響損益：損益依發生月份，不看付了沒）
            for st in self.s.scalars(select(Settlement).where(Settlement.cash_txn_id.in_((txn, f"adj:{txn}")),
                                                              Settlement.reversed_at.is_(None))):
                st.reversed_at = now
                touched.append(st.document_id)
            self.s.flush()
            for doc_id in dict.fromkeys(touched):
                doc = self.docs.get(doc_id)
                self.s.refresh(doc)
                self.settle.posting.recompute_settlement(doc)
            # 2) 由這筆交易建立的單據（待確認裡「建立已結清單據」，例如手續費）→ 作廢；已關帳的月份改開待確認
            for doc in self.s.scalars(select(Document).where(Document.source_kind == "bank_only",
                                                             Document.source_ref.in_((txn, f"adj:{txn}")),
                                                             Document.status != "void")):
                closed = [p for p in {l.period_key for l in doc.lines} if self.ops.is_closed(doc.company_id, p)]
                if closed:
                    self.ops.open_review("import_warning", doc.company_id, f"document:{doc.id}",
                                         {"document_id": str(doc.id), "message":
                                          f"{reason}，但這張單據所在的 {'、'.join(sorted(closed))} 已關帳，請確認是否要重開調整"},
                                         period_key=sorted(closed)[0])
                    flagged.append(str(doc.id))
                else:
                    self.settle.posting.void(doc.id, reason, actor, force=True)
                    voided.append(str(doc.id))
            # 3) 還沒處理的待確認撤下（重新匯入時會再比一次）
            for it in self.s.scalars(select(ReviewItem).where(ReviewItem.ref == f"cash_txn:{txn}",
                                                              ReviewItem.status == "open")):
                it.status, it.resolved_by, it.resolved_at = "dismissed", actor, now
                it.resolution = {"withdrawn": True, "reason": reason}
                dismissed += 1
            for it in self.s.scalars(select(ReviewItem).where(ReviewItem.kind == "bank_only",
                                                              ReviewItem.ref == f"cash_txn:{txn}",
                                                              ReviewItem.status != "open")):
                it.resolution = {**(it.resolution or {}), "withdrawn": True}
            found = bool(touched or voided or dismissed or flagged)
            status = "needs_review" if flagged else ("withdrawn" if found else "not_found")
            counts[status] += 1
            results.append({"cash_txn_id": txn, "status": status, "reopened_documents": [str(i) for i in dict.fromkeys(touched)],
                            "voided_documents": voided, "dismissed_reviews": dismissed, "flagged_documents": flagged})
        self.s.flush()
        return {**counts, "results": results}

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
    def _open_docs(self, company_ids: set[str], doc_type: str | tuple[str, ...], cp_id: uuid.UUID | None,
                   txn_date: date) -> list[Document]:
        types = (doc_type,) if isinstance(doc_type, str) else doc_type
        stmt = select(Document).where(
            Document.doc_type.in_(types), Document.status.in_(("posted", "draft")),
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
        for d in self._open_docs(company_ids, ("payable", "prepayment"), None, txn_date):
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
        if self._quiet(it):
            return self._skip(it, company_id, reason)
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
    return outstanding(d)


def _period(d: Document) -> str:
    return min((l.period_key for l in d.lines), default="")

