"""使用者端 API（/api/v1）。路由只做：取得使用者 → 權限 → 呼叫功能層 → 轉成回應。"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_ledger_session
from app.entry import serializers as ser
from app.entry.deps import current_user, need
from app.entry.schemas import (BatchResolveIn, BatchStoreIn, CashPaymentIn, AccrualBatchIn, AccrualTemplateIn, CloseIn, CounterpartyIn, CounterpartyPatch,
                               DocumentCreate, DocumentPatch, LinkAccrualIn, RecognizeIn, RecurringIn, ReopenIn,
                               ResolveIn, RuleIn, VoidIn)
from app.errors import AppError, Forbidden, NotFound, ValidationFailed
from app.feature.accruals import AccrualService, AdvanceService
from app.feature.cash_payment import CashPaymentService
from app.feature.common import LineInput
from app.feature.imports.parsers import looks_like_invoice_summary
from app.feature.imports.service import ImportService
from app.feature.models import AccrualTemplate, RecurringTemplate
from app.feature.periods import PeriodService
from app.feature.posting import PostingService
from app.feature.reports import ReportService
from app.feature.repositories import CatalogRepository, DocumentRepository, OpsRepository
from app.feature.review import ReviewService
from app.feature.vendor_map import VendorMapService
from app.guard.principals import UserPrincipal
from app.seams.company import get_directory

router = APIRouter(prefix="/api/v1")
PERIOD_PATTERN = r"^\d{4}-\d{2}$"


def _cids(user: UserPrincipal) -> set[str] | None:
    if user.scope.all:
        return None
    d = get_directory()
    return set(user.scope.companies) | {s.company_id for s in d.list_stores() if s.id in user.scope.stores}


def _check_company(user: UserPrincipal, company_id: str) -> None:
    cids = _cids(user)
    if company_id == "*":  # 涵蓋所有公司的批次（發票彙總表）：要能看全部公司
        if cids is not None:
            raise Forbidden("這個檔案包含所有公司，只有可以看全部公司的帳號能匯入")
        return
    if cids is not None and company_id not in cids:
        raise Forbidden("你沒有這家公司的權限")


def _doc_or_404(s: Session, user: UserPrincipal, doc_id: uuid.UUID):
    d = DocumentRepository(s).get(doc_id)
    if d is None:
        raise NotFound("單據不存在")
    _check_company(user, d.company_id)
    if user.scope.store_only and not any(l.store_id in user.scope.stores for l in d.lines):
        raise Forbidden("你沒有這家門市的權限")
    return d


# ---------------------------------------------------------------- 使用者
@router.get("/me")
def me(user: UserPrincipal = Depends(current_user)):
    return {"email": user.email, "name": user.name, "role": user.role,
            "scope": {"all": user.scope.all, "companies": sorted(user.scope.companies),
                      "stores": sorted(user.scope.stores)}}


@router.get("/directory")
def directory(user: UserPrincipal = Depends(current_user)):
    d = get_directory()
    cids = _cids(user)
    return {"companies": [c.__dict__ for c in d.list_companies() if cids is None or c.id in cids],
            "stores": [s.__dict__ for s in d.list_stores() if user.scope.allows_store(s.company_id, s.id)]}


# ---------------------------------------------------------------- 損益
@router.get("/pl/overview")
def pl_overview(period: str = Query(pattern=PERIOD_PATTERN), user: UserPrincipal = Depends(need("pl.view")),
                s: Session = Depends(get_ledger_session, scope="function")):
    return ReportService(s).overview(user.scope, period)


@router.get("/pl/statement")
def pl_statement(period_from: str = Query(pattern=PERIOD_PATTERN), period_to: str = Query(pattern=PERIOD_PATTERN), company_id: str | None = None,
                 store_id: str | None = None, columns: str = Query("store", pattern="^(store|month|company)$"),
                 eliminate_intercompany: bool | None = None, user: UserPrincipal = Depends(need("pl.view")),
                 s: Session = Depends(get_ledger_session, scope="function")):
    if company_id:
        _check_company(user, company_id)
    if user.scope.store_only:
        if not store_id or store_id not in user.scope.stores:
            raise Forbidden("請選擇你負責的門市")
    return ReportService(s).statement(user.scope, period_from=period_from, period_to=period_to,
                                      company_id=company_id, store_id=store_id, columns=columns,
                                      eliminate_intercompany=eliminate_intercompany)


@router.get("/pl/drilldown")
def pl_drilldown(period: str = Query(pattern=PERIOD_PATTERN), company_id: str | None = None, store_id: str | None = None,
                 category_code: str | None = None, pl_section: str | None = None, page: int = 1,
                 user: UserPrincipal = Depends(need("pl.view")), s: Session = Depends(get_ledger_session, scope="function")):
    if not category_code and not pl_section:
        raise ValidationFailed("請指定 category_code 或 pl_section")
    if company_id:
        _check_company(user, company_id)
    return {"items": ReportService(s).drilldown(user.scope, period=period, company_id=company_id,
                                                store_id=store_id, category_code=category_code,
                                                pl_section=pl_section, page=page)}


@router.get("/balances")
def balances(as_of: date, company_id: str | None = None, user: UserPrincipal = Depends(need("balance.view")),
             s: Session = Depends(get_ledger_session, scope="function")):
    return ReportService(s).balances(user.scope, as_of=as_of, company_id=company_id)


# ---------------------------------------------------------------- 單據
def _doc_filters(user: UserPrincipal, company_id: str | None, store_id: str | None, **rest) -> dict:
    """單據清單與合計共用：選總公司時含分公司（與損益表一致）。"""
    cids = _cids(user)
    if company_id:
        _check_company(user, company_id)
        cids = {c.id for c in get_directory().list_companies()
                if c.id == company_id or c.parent_company_id == company_id}
    if user.scope.store_only and (not store_id or store_id not in user.scope.stores):
        raise Forbidden("請選擇你負責的門市")
    return {"company_ids": cids, "store_id": store_id, **rest}


@router.get("/documents")
def list_documents(doc_type: str | None = None, company_id: str | None = None, store_id: str | None = None,
                   period: str | None = None, settle_status: str | None = None, source_kind: str | None = None,
                   due_before: date | None = None, q: str | None = None, page: int = Query(1, ge=1),
                   page_size: int = Query(50, le=200), user: UserPrincipal = Depends(need("doc.view")),
                   s: Session = Depends(get_ledger_session, scope="function")):
    f = _doc_filters(user, company_id, store_id, doc_type=doc_type, period=period, settle_status=settle_status,
                     source_kind=source_kind, due_before=due_before, q=q)
    rows, total = DocumentRepository(s).list(page=page, page_size=page_size, **f)
    return {"items": [ser.document(d, detail=False) for d in rows], "total": total, "page": page,
            "page_size": page_size}


@router.get("/documents/summary")
def documents_summary(doc_type: str | None = None, company_id: str | None = None, store_id: str | None = None,
                      period: str | None = None, settle_status: str | None = None, source_kind: str | None = None,
                      q: str | None = None, user: UserPrincipal = Depends(need("doc.view")),
                      s: Session = Depends(get_ledger_session, scope="function")):
    """清單上方的合計：算全部符合條件的單據，不受分頁影響。逾期以台北時間今天為準。"""
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=8))).date()
    f = _doc_filters(user, company_id, store_id, doc_type=doc_type, period=period, settle_status=settle_status,
                     source_kind=source_kind, q=q)
    return {**DocumentRepository(s).summary(today=today, **f), "as_of": today.isoformat()}


@router.post("/documents", status_code=201)
def create_document(body: DocumentCreate, user: UserPrincipal = Depends(need("doc.create")),
                    s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, body.company_id)
    return ser.document(PostingService(s).create(body.to_input(), user.actor))


@router.post("/documents/check")
def check_document(body: DocumentCreate, user: UserPrincipal = Depends(need("doc.create")),
                   s: Session = Depends(get_ledger_session, scope="function")):
    return {"findings": [f.to_dict() for f in PostingService(s).check(body.to_input())]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: uuid.UUID, user: UserPrincipal = Depends(need("doc.view")),
                 s: Session = Depends(get_ledger_session, scope="function")):
    return ser.document(_doc_or_404(s, user, doc_id))


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: uuid.UUID, body: DocumentPatch, user: UserPrincipal = Depends(need("doc.edit")),
                   s: Session = Depends(get_ledger_session, scope="function")):
    _doc_or_404(s, user, doc_id)
    lines = [LineInput(**l.model_dump()) for l in body.lines] if body.lines is not None else None
    d = PostingService(s).update(doc_id, body.version, user.actor, memo=body.memo, due_date=body.due_date,
                                 lines=lines, settle_amount=body.settle_amount, tax_amount=body.tax_amount)
    return ser.document(d)


@router.post("/documents/batch-store")
def batch_store(body: BatchStoreIn, user: UserPrincipal = Depends(need("doc.edit")),
                s: Session = Depends(get_ledger_session, scope="function")):
    """勾選的單據一次改到某家門市（例：先記在公司層級的薪資改到青年店）。每張各自成功或失敗。"""
    svc = PostingService(s)
    ok, failed = [], []
    for doc_id in body.document_ids:
        try:
            with s.begin_nested():
                d = _doc_or_404(s, user, doc_id)
                lines = [LineInput(l.period_key, l.category_code, l.amount, body.store_id or None, l.description) for l in d.lines]
                svc.update(doc_id, d.version, user.actor, lines=lines)
                if d.counterparty is not None and body.store_id:
                    d.counterparty.default_store_id = body.store_id   # 下次同一個對象自動帶這家門市
            ok.append(str(doc_id))
        except AppError as e:
            failed.append({"id": str(doc_id), "message": e.message})
    return {"ok": ok, "failed": failed}


@router.post("/documents/{doc_id}/void")
def void_document(doc_id: uuid.UUID, body: VoidIn, user: UserPrincipal = Depends(need("doc.void")),
                  s: Session = Depends(get_ledger_session, scope="function")):
    _doc_or_404(s, user, doc_id)
    return ser.document(PostingService(s).void(doc_id, body.reason, user.actor))


@router.post("/cash-payments", status_code=201)
def create_cash_payment(body: CashPaymentIn, user: UserPrincipal = Depends(need("doc.edit")),
                        s: Session = Depends(get_ledger_session, scope="function")):
    """應付用現金付掉：選的單據標成已付（不影響損益，只改已付／未付）。"""
    for raw in body.document_ids:
        try:
            _doc_or_404(s, user, uuid.UUID(raw))
        except ValueError:
            raise ValidationFailed(f"單據 ID 格式錯誤：{raw}")
    return CashPaymentService(s).pay(body.document_ids, body.paid_date, body.source, user.actor,
                                     note=body.note, amount=body.amount)


@router.post("/cash-payments/{payment_id}/reverse")
def reverse_cash_payment(payment_id: str, user: UserPrincipal = Depends(need("doc.edit")),
                         s: Session = Depends(get_ledger_session, scope="function")):
    return CashPaymentService(s).reverse(payment_id, user.actor)


@router.post("/documents/{doc_id}/link-accrual")
def link_accrual(doc_id: uuid.UUID, body: LinkAccrualIn, user: UserPrincipal = Depends(need("doc.edit")),
                 s: Session = Depends(get_ledger_session, scope="function")):
    _doc_or_404(s, user, doc_id)
    j = AccrualService(s).link(doc_id, body.accrual_id, body.relation, user.actor)
    return {"journal": ser.document(j) if j else None}


# ---------------------------------------------------------------- 預估、預收
@router.get("/accruals/suggestions")
def accrual_suggestions(company_id: str, period: str = Query(pattern=PERIOD_PATTERN), user: UserPrincipal = Depends(need("doc.create")),
                        s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, company_id)
    return {"items": [x.to_dict() for x in AccrualService(s).suggestions(company_id, period)]}


@router.post("/accruals/batch", status_code=201)
def accrual_batch(body: AccrualBatchIn, user: UserPrincipal = Depends(need("doc.create")),
                  s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, body.company_id)
    svc = AccrualService(s)
    return {"items": [ser.document(svc.create_estimate(i.template_id, body.period, i.amount, user.actor))
                      for i in body.items]}


@router.get("/advances")
def advances(company_id: str | None = None, status: str | None = None, delivery_before: date | None = None,
             user: UserPrincipal = Depends(need("doc.view")), s: Session = Depends(get_ledger_session, scope="function")):
    cids = {company_id} if company_id else _cids(user)
    if company_id:
        _check_company(user, company_id)
    return {"items": [ser.document(d, detail=False) | {"recognized_amount": d.recognized_amount,
                                                       "expected_delivery_date": d.expected_delivery_date.isoformat()
                                                       if d.expected_delivery_date else None}
                      for d in AdvanceService(s).list(cids, status, delivery_before)]}


@router.post("/advances/recognize")
def recognize(body: RecognizeIn, user: UserPrincipal = Depends(need("doc.create")),
              s: Session = Depends(get_ledger_session, scope="function")):
    svc = AdvanceService(s)
    out = []
    for it in body.items:
        d = DocumentRepository(s).get(it.document_id)
        if d is not None:
            _check_company(user, d.company_id)
        out.append(ser.document(svc.recognize(it.document_id, it.amount, it.period, it.category_code, user.actor,
                                              store_id=it.store_id)))
    return {"items": out}


# ---------------------------------------------------------------- 匯入
@router.post("/imports", status_code=201)
async def upload_import(kind: str = Form(...), company_id: str = Form("*"), period: str = Form(...),
                        store_id: str | None = Form(None), file: UploadFile = File(...),
                        user: UserPrincipal = Depends(need("import.run")), s: Session = Depends(get_ledger_session, scope="function")):
    data = await file.read()
    if kind == "ap_sheet" and looks_like_invoice_summary(data):
        kind = "invoice_summary"  # 選了應付表但其實是發票系統的彙總表
    if kind == "invoice_summary":
        company_id = "*"
    _check_company(user, company_id)
    svc = ImportService(s)
    return svc.serialize(svc.upload(kind=kind, company_id=company_id, period=period, store_id=store_id,
                                    file_name=file.filename or "upload.xlsx", data=data, actor=user.actor))


@router.get("/imports/{batch_id}")
def get_import(batch_id: uuid.UUID, user: UserPrincipal = Depends(need("import.run")),
               s: Session = Depends(get_ledger_session, scope="function")):
    svc = ImportService(s)
    b = OpsRepository(s).batch(batch_id)
    if b is None:
        raise NotFound("找不到匯入批次")
    _check_company(user, b.company_id)
    return svc.serialize(b)


@router.post("/imports/{batch_id}/commit")
def commit_import(batch_id: uuid.UUID, user: UserPrincipal = Depends(need("import.run")),
                  s: Session = Depends(get_ledger_session, scope="function")):
    b = OpsRepository(s).batch(batch_id)
    if b is None:
        raise NotFound("找不到匯入批次")
    _check_company(user, b.company_id)
    return ImportService(s).commit(batch_id, user.actor)


# ---------------------------------------------------------------- 待確認
@router.get("/review-items")
def review_items(kind: str | None = None, company_id: str | None = None, period: str | None = None,
                 user: UserPrincipal = Depends(need("review.resolve")), s: Session = Depends(get_ledger_session, scope="function")):
    cids = {company_id} if company_id else _cids(user)
    items = OpsRepository(s).list_reviews(company_ids=cids, kind=kind, period=period)
    # 銀行交易：對方戶名先前選過門市的，帶出建議門市（例：員工屬於哪家店）
    names = {(i.payload or {}).get("item", {}).get("counterparty_name") for i in items if i.kind == "bank_only"} - {None, ""}
    stores = {cp.name: cp.default_store_id for cp in CatalogRepository(s).counterparties_by_names(names)} if names else {}
    def extra(i):
        if i.kind != "bank_only":
            return {}
        return {"suggested_store_id": stores.get((i.payload or {}).get("item", {}).get("counterparty_name"))}
    return {"items": [{"id": str(i.id), "kind": i.kind, "company_id": i.company_id, "period": i.period_key,
                       "ref": i.ref, "payload": i.payload, "created_at": i.created_at.isoformat(), **extra(i)} for i in items]}


@router.post("/review-items/batch")
def batch_resolve(body: BatchResolveIn, user: UserPrincipal = Depends(need("review.resolve")),
                  s: Session = Depends(get_ledger_session, scope="function")):
    """批次處理待確認：例如勾選 9 月的薪資匯款，一次建立 P01 薪資單據、損益記在 8 月。"""
    return ReviewService(s).batch(body.ids, body.action, body.payload, user.actor,
                                  allowed=lambda cid: user.scope.allows_company(cid))


@router.post("/review-items/{item_id}/resolve")
def resolve_review(item_id: uuid.UUID, body: ResolveIn, user: UserPrincipal = Depends(need("review.resolve")),
                   s: Session = Depends(get_ledger_session, scope="function")):
    item = OpsRepository(s).review(item_id)
    if item is None:
        raise NotFound("待確認項目不存在")
    _check_company(user, item.company_id)
    return ReviewService(s).resolve(item_id, body.action, body.payload, user.actor)


# ---------------------------------------------------------------- 月結
@router.get("/periods/{company_id}/{period}")
def period_checklist(company_id: str, period: str, acknowledge_unallocated: bool = False,
                     user: UserPrincipal = Depends(need("pl.view")), s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, company_id)
    st = OpsRepository(s).period(company_id, period)
    return {"company_id": company_id, "period": period, "status": st.status,
            "checklist": PeriodService(s).checklist(company_id, period, acknowledge_unallocated)}


@router.post("/periods/{company_id}/{period}/close")
def close_period(company_id: str, period: str, body: CloseIn, user: UserPrincipal = Depends(need("period.close")),
                 s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, company_id)
    return PeriodService(s).close(company_id, period, user.actor, body.acknowledge_unallocated)


@router.post("/periods/{company_id}/{period}/reopen")
def reopen_period(company_id: str, period: str, body: ReopenIn, user: UserPrincipal = Depends(need("period.reopen")),
                  s: Session = Depends(get_ledger_session, scope="function")):
    return PeriodService(s).reopen(company_id, period, user.actor, body.reason)


# ---------------------------------------------------------------- 設定
@router.get("/settings/categories")
def categories(user: UserPrincipal = Depends(current_user), s: Session = Depends(get_ledger_session, scope="function")):
    return {"items": [{"code": c.code, "name": c.name, "pl_section": c.pl_section, "nature": c.nature,
                       "is_active": c.is_active} for c in CatalogRepository(s).categories.values()]}


@router.get("/settings/counterparties")
def list_counterparties(q: str | None = None, user: UserPrincipal = Depends(need("doc.view")),
                        s: Session = Depends(get_ledger_session, scope="function")):
    return {"items": [{"id": str(c.id), "name": c.name, "tax_id": c.tax_id, "default_category_code":
                       c.default_category_code, "payment_rule": c.payment_rule, "keywords": c.keywords,
                       "is_intercompany": c.is_intercompany, "is_active": c.is_active}
                      for c in CatalogRepository(s).list_counterparties(q)]}


@router.post("/settings/counterparties", status_code=201)
def create_counterparty(body: CounterpartyIn, user: UserPrincipal = Depends(need("settings.manage")),
                        s: Session = Depends(get_ledger_session, scope="function")):
    cat = CatalogRepository(s)
    if body.default_category_code and cat.category(body.default_category_code) is None:
        raise ValidationFailed("預設類別不存在")
    cp = cat.add_counterparty(**body.model_dump())
    return {"id": str(cp.id), "name": cp.name}


@router.patch("/settings/counterparties/{cp_id}")
def patch_counterparty(cp_id: uuid.UUID, body: CounterpartyPatch, user: UserPrincipal = Depends(need("settings.manage")),
                       s: Session = Depends(get_ledger_session, scope="function")):
    cp = CatalogRepository(s).counterparty(cp_id)
    if cp is None:
        raise NotFound("對象不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(cp, k, v)
    return {"id": str(cp.id), "name": cp.name}


# ---------------------------------------------------------------- 廠商類別對應表
@router.get("/settings/vendor-map")
def vendor_map(q: str | None = None, only_unmapped: bool = False, user: UserPrincipal = Depends(need("doc.view")),
               s: Session = Depends(get_ledger_session, scope="function")):
    return {"items": VendorMapService(s).list(q, only_unmapped)}


@router.patch("/settings/vendor-map/{cp_id}")
def set_vendor_category(cp_id: uuid.UUID, category_code: str | None = Body(None, embed=True),
                        name: str | None = Body(None, embed=True),
                        user: UserPrincipal = Depends(need("settings.manage")), s: Session = Depends(get_ledger_session, scope="function")):
    return VendorMapService(s).set(cp_id, category_code, user.actor, name=name)


@router.post("/settings/vendor-map/import")
async def import_vendor_map(file: UploadFile = File(...), user: UserPrincipal = Depends(need("settings.manage")),
                            s: Session = Depends(get_ledger_session, scope="function")):
    from dataclasses import asdict
    return asdict(VendorMapService(s).import_xlsx(await file.read(), user.actor))


@router.get("/settings/vendor-map/export")
def export_vendor_map(user: UserPrincipal = Depends(need("doc.view")), s: Session = Depends(get_ledger_session, scope="function")):
    return Response(VendorMapService(s).export_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''vendor-categories.xlsx"})


@router.post("/settings/rules", status_code=201)
def create_rule(body: RuleIn, user: UserPrincipal = Depends(need("settings.manage")),
                s: Session = Depends(get_ledger_session, scope="function")):
    r = CatalogRepository(s).add_rule(body.layer, body.category_code, user.actor, body.counterparty_id, body.keyword)
    return {"id": str(r.id)}


@router.get("/settings/recurring")
def list_recurring(user: UserPrincipal = Depends(need("settings.manage")), s: Session = Depends(get_ledger_session, scope="function")):
    return {"items": [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in t.__dict__.items()
                       if not k.startswith("_")} for t in OpsRepository(s).list_recurring(_cids(user))]}


@router.post("/settings/recurring", status_code=201)
def create_recurring(body: RecurringIn, user: UserPrincipal = Depends(need("settings.manage")),
                     s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, body.company_id)
    t = OpsRepository(s).add(RecurringTemplate(**body.model_dump()))
    return {"id": str(t.id)}


@router.get("/settings/accrual-templates")
def list_accrual_templates(user: UserPrincipal = Depends(need("settings.manage")),
                           s: Session = Depends(get_ledger_session, scope="function")):
    return {"items": [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in t.__dict__.items()
                       if not k.startswith("_")} for t in OpsRepository(s).list_accrual_templates(_cids(user))]}


@router.post("/settings/accrual-templates", status_code=201)
def create_accrual_template(body: AccrualTemplateIn, user: UserPrincipal = Depends(need("settings.manage")),
                            s: Session = Depends(get_ledger_session, scope="function")):
    _check_company(user, body.company_id)
    if body.accrual_mode == "provision" and not body.fixed_amount:
        raise ValidationFailed("分月預提要填每月金額")
    t = OpsRepository(s).add(AccrualTemplate(**body.model_dump()))
    return {"id": str(t.id)}
