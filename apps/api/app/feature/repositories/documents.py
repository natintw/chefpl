"""單據、明細、結清。損益與餘額彙總只查 v_pl_lines、v_balance_lines 兩個 view。"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, any_, case, func, literal, or_, select, text
from sqlalchemy.orm import Session

from app.feature.models import Counterparty, Document, DocumentLine, Settlement


class DocumentRepository:
    def __init__(self, session: Session):
        self.s = session

    # ----- 單據 -----
    def add(self, doc: Document) -> Document:
        self.s.add(doc)
        self.s.flush()
        return doc

    def get(self, doc_id: uuid.UUID, lock: bool = False) -> Document | None:
        if lock:
            return self.s.scalar(select(Document).where(Document.id == doc_id).with_for_update(of=Document))
        return self.s.get(Document, doc_id)

    def get_by_source(self, source_kind: str, source_ref: str) -> Document | None:
        return self.s.scalar(select(Document).where(
            Document.source_kind == source_kind, Document.source_ref == source_ref, Document.status != "void"))

    def find_by_invoice(self, invoice_no: str, counterparty_id: uuid.UUID | None = None,
                        seller_tax_id: str | None = None, exclude_id: uuid.UUID | None = None) -> Document | None:
        stmt = select(Document).where(Document.status != "void", literal(invoice_no) == any_(Document.invoice_nos))
        if counterparty_id is not None:
            stmt = stmt.where(Document.counterparty_id == counterparty_id)
        elif seller_tax_id:
            stmt = stmt.join(Counterparty, Counterparty.id == Document.counterparty_id).where(
                Counterparty.tax_id == seller_tax_id)
        if exclude_id is not None:
            stmt = stmt.where(Document.id != exclude_id)
        return self.s.scalar(stmt.limit(1))

    def related(self, doc_id: uuid.UUID, relation: str | None = None) -> list[Document]:
        stmt = select(Document).where(Document.related_document_id == doc_id, Document.status != "void")
        if relation:
            stmt = stmt.where(Document.relation == relation)
        return list(self.s.scalars(stmt))

    def _filtered(self, *, company_ids: set[str] | None = None, doc_type: str | None = None,
                  period: str | None = None, settle_status: str | None = None, source_kind: str | None = None,
                  status: str | None = None, store_id: str | None = None, due_before: date | None = None,
                  q: str | None = None):
        """清單與合計共用的篩選條件。store_id="unallocated" 表示公司層級（明細沒有門市）。"""
        stmt = select(Document)
        conds = []
        if company_ids is not None:
            conds.append(Document.company_id.in_(company_ids))
        if doc_type:
            conds.append(Document.doc_type.in_(doc_type.split(",")))
        if settle_status:
            conds.append(Document.settle_status.in_(settle_status.split(",")))
        if source_kind:
            conds.append(Document.source_kind.in_(source_kind.split(",")))
        conds.append(Document.status == status if status else Document.status != "void")
        if due_before:
            conds.append(Document.due_date <= due_before)
        if period or store_id:
            sub = select(DocumentLine.document_id)
            if period:
                sub = sub.where(DocumentLine.period_key == period)
            if store_id == "unallocated":
                sub = sub.where(DocumentLine.store_id.is_(None))
            elif store_id:
                sub = sub.where(DocumentLine.store_id == store_id)
            conds.append(Document.id.in_(sub))
        if q:
            stmt = stmt.outerjoin(Counterparty, Counterparty.id == Document.counterparty_id)
            conds.append(or_(Counterparty.name.ilike(f"%{q}%"), Document.memo.ilike(f"%{q}%"),
                             literal(q) == any_(Document.invoice_nos)))
        return stmt.where(and_(*conds))

    def list(self, *, page: int = 1, page_size: int = 50, **filters) -> tuple[list[Document], int]:
        stmt = self._filtered(**filters)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.s.scalars(stmt.order_by(Document.doc_date.desc(), Document.created_at.desc())
                              .offset((page - 1) * page_size).limit(page_size)).unique().all()
        return list(rows), total

    def summary(self, *, today: date, **filters) -> dict[str, int]:
        """符合篩選條件的「全部」單據合計（不受分頁影響）。"""
        sub = self._filtered(**filters).subquery()
        outstanding = func.coalesce(sub.c.settle_amount, 0) - sub.c.settled_amount
        is_open = sub.c.settle_status.in_(("open", "partial"))
        overdue = and_(is_open, sub.c.due_date < today, outstanding > 0)
        ic = select(Counterparty.id).where(Counterparty.is_intercompany.is_(True)).scalar_subquery()
        row = self.s.execute(select(
            func.count(),
            func.coalesce(func.sum(sub.c.settle_amount), 0),
            func.coalesce(func.sum(sub.c.settled_amount), 0),
            func.coalesce(func.sum(case((is_open, outstanding), else_=0)), 0),
            func.coalesce(func.sum(case((overdue, 1), else_=0)), 0),
            func.coalesce(func.sum(case((overdue, outstanding), else_=0)), 0),
            func.coalesce(func.sum(case((and_(is_open, sub.c.counterparty_id.in_(ic)), outstanding), else_=0)), 0),
            func.coalesce(func.sum(case((sub.c.status == "draft", 1), else_=0)), 0),
        ).select_from(sub)).one()
        keys = ("count", "total", "settled", "outstanding", "overdue_count", "overdue_amount",
                "intercompany_outstanding", "drafts")
        return {k: int(v or 0) for k, v in zip(keys, row)}

    def open_items(self, *, company_id: str | None, doc_types: list[str], due_before: date | None,
                   updated_after: datetime | None, after_key: tuple[datetime, uuid.UUID] | None,
                   limit: int) -> list[Document]:
        stmt = select(Document).where(Document.doc_type.in_(doc_types), Document.status == "posted",
                                      Document.settle_status.in_(["open", "partial"]))
        if company_id:
            stmt = stmt.where(Document.company_id == company_id)
        if due_before:
            stmt = stmt.where(or_(Document.due_date.is_(None), Document.due_date <= due_before))
        if updated_after:
            stmt = stmt.where(Document.updated_at > updated_after)
        if after_key:
            stmt = stmt.where(or_(Document.updated_at > after_key[0],
                                  and_(Document.updated_at == after_key[0], Document.id > after_key[1])))
        return list(self.s.scalars(stmt.order_by(Document.updated_at, Document.id).limit(limit)).unique())

    def removed_since(self, *, company_id: str | None, doc_types: list[str], updated_after: datetime) -> list[uuid.UUID]:
        stmt = select(Document.id).where(Document.doc_type.in_(doc_types), Document.updated_at > updated_after,
                                         or_(Document.status == "void",
                                             Document.settle_status.in_(["settled", "over"])))
        if company_id:
            stmt = stmt.where(Document.company_id == company_id)
        return list(self.s.scalars(stmt))

    # ----- 結清 -----
    def active_settlement(self, doc_id: uuid.UUID, cash_txn_id: str) -> Settlement | None:
        return self.s.scalar(select(Settlement).where(Settlement.document_id == doc_id,
                                                      Settlement.cash_txn_id == cash_txn_id,
                                                      Settlement.reversed_at.is_(None)))

    def allocated_for_txn(self, cash_txn_id: str) -> int:
        return self.s.scalar(select(func.coalesce(func.sum(Settlement.amount), 0)).where(
            Settlement.cash_txn_id == cash_txn_id, Settlement.reversed_at.is_(None))) or 0

    def txn_already_used(self, cash_txn_id: str) -> bool:
        return self.allocated_for_txn(cash_txn_id) > 0

    def add_settlement(self, st: Settlement) -> None:
        self.s.add(st)
        self.s.flush()

    # ----- 彙總（只透過 view）-----
    def pl_aggregate(self, *, company_ids: set[str] | None, store_id: str | None, period_from: str,
                     period_to: str, group_by: str) -> list[dict[str, Any]]:
        """group_by：store / month / company。回傳 {col, company_id, store_id, category_code, nature, pl_section,
        amount, is_intercompany}。"""
        col = {"store": "coalesce(store_id, '__company__:' || company_id)", "month": "period_key",
               "company": "company_id"}[group_by]
        where = ["period_key BETWEEN :pf AND :pt"]
        params: dict[str, Any] = {"pf": period_from, "pt": period_to}
        if company_ids is not None:
            where.append("company_id = ANY(:cids)")
            params["cids"] = list(company_ids)
        if store_id == "unallocated":
            where.append("store_id IS NULL")
        elif store_id:
            where.append("store_id = :sid")
            params["sid"] = store_id
        sql = f"""
          SELECT {col} AS col, category_code, nature, pl_section, is_intercompany, sum(amount)::bigint AS amount
          FROM ledger.v_pl_lines WHERE {' AND '.join(where)}
          GROUP BY 1, 2, 3, 4, 5"""
        return [dict(r._mapping) for r in self.s.execute(text(sql), params)]

    def pl_lines(self, *, company_ids: set[str] | None, store_id: str | None, period: str,
                 category_codes: list[str], limit: int, offset: int) -> list[dict[str, Any]]:
        where = ["v.period_key = :p", "v.category_code = ANY(:cats)"]
        params: dict[str, Any] = {"p": period, "cats": category_codes, "lim": limit, "off": offset}
        if company_ids is not None:
            where.append("v.company_id = ANY(:cids)")
            params["cids"] = list(company_ids)
        if store_id == "unallocated":
            where.append("v.store_id IS NULL")
        elif store_id:
            where.append("v.store_id = :sid")
            params["sid"] = store_id
        sql = f"""
          SELECT v.document_id, v.line_no, v.company_id, v.store_id, v.category_code, v.amount, v.description,
                 v.doc_date, v.source_kind, v.doc_type, d.invoice_nos, cp.name AS counterparty_name, d.memo
          FROM ledger.v_pl_lines v
          JOIN ledger.documents d ON d.id = v.document_id
          LEFT JOIN ledger.counterparties cp ON cp.id = d.counterparty_id
          WHERE {' AND '.join(where)}
          ORDER BY v.amount DESC LIMIT :lim OFFSET :off"""
        return [dict(r._mapping) for r in self.s.execute(text(sql), params)]

    def balance_aggregate(self, *, company_ids: set[str] | None, as_of: date) -> list[dict[str, Any]]:
        where, params = ["doc_date <= :asof"], {"asof": as_of}
        if company_ids is not None:
            where.append("company_id = ANY(:cids)")
            params["cids"] = list(company_ids)
        sql = f"""
          SELECT v.company_id, v.category_code, v.counterparty_id, cp.name AS counterparty_name,
                 cp.intercompany_company_id, sum(v.amount)::bigint AS amount
          FROM ledger.v_balance_lines v
          LEFT JOIN ledger.counterparties cp ON cp.id = v.counterparty_id
          WHERE {' AND '.join(where)}
          GROUP BY 1, 2, 3, 4, 5"""
        return [dict(r._mapping) for r in self.s.execute(text(sql), params)]

    def advance_balance(self, company_ids: set[str] | None) -> dict[str, int]:
        stmt = select(Document.company_id, func.sum(Document.settle_amount - Document.recognized_amount)).where(
            Document.doc_type == "advance_receipt", Document.status == "posted").group_by(Document.company_id)
        if company_ids is not None:
            stmt = stmt.where(Document.company_id.in_(company_ids))
        return {c: int(v or 0) for c, v in self.s.execute(stmt)}

    def has_lines(self, *, company_id: str, period: str, source_kind: str | None = None,
                  store_id: str | None = None, category_code: str | None = None,
                  counterparty_id: uuid.UUID | None = None, exclude_doc_types: tuple[str, ...] = ()) -> bool:
        stmt = select(func.count()).select_from(DocumentLine).join(Document).where(
            Document.status == "posted", DocumentLine.company_id == company_id, DocumentLine.period_key == period)
        if source_kind:
            stmt = stmt.where(Document.source_kind == source_kind)
        if store_id:
            stmt = stmt.where(DocumentLine.store_id == store_id)
        if category_code:
            stmt = stmt.where(DocumentLine.category_code == category_code)
        if counterparty_id:
            stmt = stmt.where(Document.counterparty_id == counterparty_id)
        if exclude_doc_types:
            stmt = stmt.where(Document.doc_type.not_in(exclude_doc_types))
        return (self.s.scalar(stmt) or 0) > 0

    def sum_lines(self, *, company_id: str, periods: list[str], category_code: str,
                  store_id: str | None, counterparty_id: uuid.UUID | None,
                  doc_types: tuple[str, ...] = ("payable", "receivable", "journal")) -> dict[str, int]:
        stmt = (select(DocumentLine.period_key, func.sum(DocumentLine.amount)).join(Document)
                .where(Document.status == "posted", DocumentLine.company_id == company_id,
                       DocumentLine.period_key.in_(periods), DocumentLine.category_code == category_code,
                       Document.doc_type.in_(doc_types), Document.relation.is_(None))
                .group_by(DocumentLine.period_key))
        if store_id:
            stmt = stmt.where(DocumentLine.store_id == store_id)
        if counterparty_id:
            stmt = stmt.where(Document.counterparty_id == counterparty_id)
        return {p: int(v) for p, v in self.s.execute(stmt)}

    def provision_documents(self, template_id: uuid.UUID) -> list[Document]:
        return list(self.s.scalars(select(Document).where(
            Document.accrual_template_id == template_id, Document.status == "posted")))
