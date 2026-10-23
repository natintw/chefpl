"use client";
/**
 * S5 應付帳款（同頁切換應收、預收）。資料：GET /documents（分頁）、GET /documents/summary（合計，算全部符合條件的單據）。
 * 付款狀態由現金流系統回寫，這裡不手動改。選總公司時含分公司（與損益表一致）。
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { DocumentDrawer } from "@/components/DocumentDrawer";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, ScopePicker, useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import { money, settleLabel, SOURCE_LABEL } from "@/lib/format";
import type { Doc } from "@/lib/types";

const TYPES = { payable: "應付", receivable: "應收", advance_receipt: "預收" } as const;
type T = keyof typeof TYPES;
const PAGE = 100;
type Summary = { count: number; total: number; settled: number; outstanding: number; overdue_count: number;
  overdue_amount: number; intercompany_outstanding: number; drafts: number; as_of: string };

export default function PayablesPage() {
  const q = useQuery();
  const type = (q.get("type") as T) || "payable";
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [docId, setDocId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const { companyName, storeName } = useDirectory();
  const filters = {
    doc_type: type, period: type === "advance_receipt" ? undefined : q.period, company_id: q.company || undefined,
    store_id: q.store || undefined, settle_status: status || undefined, q: search || undefined,
  };
  const fkey = JSON.stringify(filters);
  useEffect(() => setPage(1), [fkey]);  // 篩選改了回到第一頁
  const list = useApi<{ items: Doc[]; total: number }>("/documents", { ...filters, page, page_size: PAGE });
  const summary = useApi<Summary>("/documents/summary", filters);
  const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
  const items = list.data?.items || [];
  const sm = summary.data;
  const pages = Math.max(1, Math.ceil((list.data?.total || 0) / PAGE));
  const filtered = !!(status || search || q.store);

  return (
    <>
      <PageHeader crumb="帳務" title={`${TYPES[type]}帳款`} sub="付款狀態由現金流系統核對銀行簿子後回寫，這裡不手動改。">
        {type !== "advance_receipt" && <PeriodPicker />}
        <ScopePicker />
        <Link className="btn pri" href={q.keep("/documents/new")}>新增</Link>
      </PageHeader>

      <div className="seg" role="group" aria-label="類型" style={{ marginBottom: 16 }}>
        {(Object.keys(TYPES) as T[]).map((t) => <button key={t} aria-pressed={type === t} onClick={() => q.set({ type: t === "payable" ? null : t })}>{TYPES[t]}</button>)}
      </div>

      {summary.error && <ErrorBox error={summary.error} onRetry={summary.reload} />}
      {sm && (
        <section className="grid-kpi" aria-label="摘要">
          <div className="card pad kpi"><div className="k">{TYPES[type]}合計（含稅）</div><div className="v" style={{ fontSize: 22 }}>{money(sm.total)}</div>
            <div className="small">{sm.count} 筆{sm.drafts ? `・其中草稿 ${sm.drafts}` : ""}</div></div>
          <div className="card pad kpi"><div className="k">{type === "payable" ? "已付" : "已收"}</div><div className="v" style={{ fontSize: 22 }}>{money(sm.settled)}</div></div>
          <div className={`card pad kpi ${sm.overdue_amount ? "alert" : ""}`}><div className="k">未結清・其中逾期</div>
            <div className="v" style={{ fontSize: 22 }}>{money(sm.outstanding)}</div>
            <div className="small">{sm.overdue_count ? <span className="neg">逾期 {sm.overdue_count} 筆 {money(sm.overdue_amount)}</span> : "沒有逾期"}
              <span className="sub">（以 {sm.as_of} 計）</span></div></div>
          <div className="card pad kpi"><div className="k">未結清中集團內</div><div className="v" style={{ fontSize: 22 }}>{money(sm.intercompany_outstanding)}</div>
            <div className="small">對外未結清 {money(sm.outstanding - sm.intercompany_outstanding)}</div></div>
        </section>
      )}

      <div className="row">
        <section className="card grow" aria-label="清單">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
            <label style={{ flex: "1 1 240px" }}><span className="sr">搜尋對象、摘要或發票號碼</span>
              <input placeholder="搜尋對象、摘要或發票號碼" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
            <label className="ctl"><span className="k">狀態</span>
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">全部</option><option value="open,partial">未結清（含部分）</option><option value="open">完全未付</option>
                <option value="partial">部分</option><option value="settled">已結清</option>
              </select></label>
          </div>
          {list.loading && !list.data && <Skeleton rows={8} />}
          {list.error && <div className="pad"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
          {list.data && items.length === 0 && (
            <Empty title={filtered ? "沒有符合的單據" : `${q.period} 沒有${TYPES[type]}單據`}
                   action={filtered ? <button className="btn" onClick={() => { setStatus(""); setSearch(""); q.set({ store: null }); }}>清除篩選</button> : undefined} />
          )}
          {items.length > 0 && (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>對象</th><th>公司／門市</th><th>發票</th><th>來源</th><th>到期日</th><th className="r">金額</th><th className="r">已結清</th><th className="r">未結清</th><th>狀態</th></tr></thead>
                <tbody>
                  {items.map((d) => {
                    const [label, cls] = settleLabel(d.settle_status, d.doc_type);
                    const overdue = d.due_date && d.due_date < today && (d.outstanding || 0) > 0;
                    return (
                      <tr key={d.id} className={`clickable ${docId === d.id ? "sel" : ""}`} onClick={() => setDocId(d.id)}>
                        <td><div style={{ fontWeight: 500 }}>{d.counterparty?.name || "（未指定）"}</div>
                          <div className="small">{d.memo || d.period_keys.join("、")}{d.status === "draft" ? "・草稿" : ""}</div></td>
                        <td className="small">{q.company ? "" : <div>{companyName(d.company_id)}</div>}
                          <div>{d.store_ids.length ? d.store_ids.map((x) => storeName(x)).join("、") : "公司層級"}</div></td>
                        <td className="num small">{d.invoice_nos.length ? d.invoice_nos.join(" ") : d.has_invoice ? "有" : "無發票"}</td>
                        <td><Tag kind="src">{SOURCE_LABEL[d.source_kind] || d.source_kind}</Tag></td>
                        <td className="num">{d.due_date || "—"}</td>
                        <td className="num r">{money(d.settle_amount)}</td>
                        <td className="num r">{money(d.settled_amount)}</td>
                        <td className="num r">{money(d.outstanding)}</td>
                        <td><span className={`tag ${cls}`}>{label}</span>{overdue && <div className="small neg">已逾期</div>}</td>
                      </tr>
                    );
                  })}
                  {sm && <tr className="sum"><td colSpan={5}>全部 {sm.count} 筆合計</td><td className="num r">{money(sm.total)}</td>
                    <td className="num r">{money(sm.settled)}</td><td className="num r">{money(sm.outstanding)}</td><td /></tr>}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end", padding: "10px 14px" }}>
              <span className="small">第 {(page - 1) * PAGE + 1}–{Math.min(page * PAGE, list.data?.total || 0)} 筆，共 {list.data?.total} 筆</span>
              <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一頁</button>
              <span className="small num">{page} / {pages}</span>
              <button className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一頁</button>
            </div>
          )}
        </section>
        {docId && <DocumentDrawer id={docId} onClose={() => setDocId(null)} />}
      </div>
    </>
  );
}
