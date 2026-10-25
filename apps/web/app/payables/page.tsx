"use client";
/**
 * S5 應付帳款（同頁切換應收、預收）。資料：GET /documents（分頁）、GET /documents/summary（合計，算全部符合條件的單據）。
 * 銀行付款由現金流系統回寫；現金付款（零用金、門市收銀、代墊）在這裡勾選後標記（POST /cash-payments）。
 * 選總公司時含分公司（與損益表一致）。
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { DocumentDrawer, type RelinkTarget } from "@/components/DocumentDrawer";
import { ManualMatch } from "@/components/ManualMatch";
import { BankLinkPlan, type LinkPlan } from "@/components/BankLinkPlan";
import { DownloadButton } from "@/components/DownloadButton";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, ScopePicker, useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, FieldErrors, Skeleton, Tag, useToast } from "@/components/ui";
import { ApiError, apiSend, useApi } from "@/lib/api";
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
  const [source, setSource] = useState("");
  const [docId, setDocId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState<Map<string, Doc>>(new Map());
  const [relink, setRelink] = useState<RelinkTarget | null>(null);
  const [linkPlan, setLinkPlan] = useState<{ plan: LinkPlan; title: string } | null>(null);
  const [planning, setPlanning] = useState(false);
  async function planFrom(path: string, body: unknown, title: string) {
    setPlanning(true);
    try { setLinkPlan({ plan: await apiSend<LinkPlan>("POST", path, body), title }); window.scrollTo({ top: 0, behavior: "smooth" }); }
    catch (e) { toast((e as Error).message || "配對失敗", "error"); } finally { setPlanning(false); }
  }
  const bankDoc = (d: Doc) => d.status !== "void" && !!d.bank_txn_ids?.length;
  const pickedDocs = [...picked.values()];
  const mixed = pickedDocs.some(bankDoc) && pickedDocs.some((d) => !bankDoc(d) && (d.outstanding || 0) > 0);
  const relinkRow = (d: Doc) => setRelink({ doc: d, bank: { cash_txn_id: d.bank_txn_ids![0], company_id: d.company_id,
    direction: d.doc_type === "payable" ? "out" : "in", amount: d.settle_amount || 0 } });
  const toast = useToast();
  const { companyName, storeName } = useDirectory();
  const filters = {
    doc_type: type, period: type === "advance_receipt" ? undefined : q.period, company_id: q.company || undefined,
    store_id: q.store || undefined, settle_status: status || undefined, q: search || undefined,
    source_kind: source || undefined,
  };
  const fkey = JSON.stringify(filters);
  useEffect(() => { setPage(1); setPicked(new Map()); }, [fkey]);  // 篩選改了回到第一頁、清掉勾選
  const list = useApi<{ items: Doc[]; total: number }>("/documents", { ...filters, page, page_size: PAGE });
  const summary = useApi<Summary>("/documents/summary", filters);
  const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
  const items = list.data?.items || [];
  const sm = summary.data;
  const pages = Math.max(1, Math.ceil((list.data?.total || 0) / PAGE));
  const filtered = !!(status || search || q.store || source);
  const canPay = type !== "advance_receipt";
  const payable = (d: Doc) => d.status !== "void" && (d.outstanding || 0) > 0;
  const selectable = (d: Doc) => d.status !== "void";   // 改門市：已付的也可以選；現金付款只處理未結清的
  const pageOpen = items.filter(selectable);
  const allPicked = pageOpen.length > 0 && pageOpen.every((d) => picked.has(d.id));
  const toggle = (d: Doc) => setPicked((m) => { const n = new Map(m); n.has(d.id) ? n.delete(d.id) : n.set(d.id, d); return n; });
  const toggleAll = () => setPicked((m) => {
    const n = new Map(m);
    pageOpen.forEach((d) => (allPicked ? n.delete(d.id) : n.set(d.id, d)));
    return n;
  });
  const refresh = () => { list.reload(); summary.reload(); };

  return (
    <>
      <PageHeader crumb="帳務" title={`${TYPES[type]}帳款`} sub="銀行付款由現金流系統核對銀行簿子後自動回寫；現金付的，勾選後按「現金付款」。">
        {type !== "advance_receipt" && <PeriodPicker />}
        <ScopePicker />
        <DownloadButton path="/documents/export" params={filters}
                        filename={`${q.store ? storeName(q.store) : q.company ? companyName(q.company) : "集團"}_${TYPES[type]}帳款_${filters.period || "全部"}.xlsx`} />
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
            <label className="ctl"><span className="k">來源</span>
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">全部</option><option value="pos">POS</option><option value="bank_only">銀行建立</option>
                <option value="invoice,invoice_summary">發票</option><option value="manual">人工</option>
              </select></label>
            {canPay && <button className="btn" disabled={planning} title="找出「來源：銀行」的單據對應的 POS／發票單據（依銀行備註寫的日期，或附近同金額）"
              onClick={() => planFrom("/documents/auto-link-plan", { company_id: q.company || undefined, store_id: q.store || undefined,
                period: q.period || undefined, doc_type: type === "payable" ? "payable" : "receivable" },
                `自動配對：銀行建立的${TYPES[type]} ↔ ${type === "payable" ? "發票" : "POS"}單據（${q.period || "全部月份"}）`)}>
              {planning ? "配對中…" : `自動配對銀行與${type === "payable" ? "發票" : " POS"}`}</button>}
          </div>
          {linkPlan && <BankLinkPlan plan={linkPlan.plan} title={linkPlan.title} receivable={type !== "payable"}
                                     onClose={() => setLinkPlan(null)} onDone={() => { setLinkPlan(null); setPicked(new Map()); refresh(); }} />}
          {canPay && mixed && (
            <div className="ai" style={{ margin: "12px 14px", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <span className="small">勾了 {[...picked.values()].filter(bankDoc).length} 張銀行單據和 {[...picked.values()].filter((d) => !bankDoc(d)).length} 張其他單據，
                可以依金額連在一起（例：9/24 存入的現金 ↔ 9/23 POS 門市現金）。</span>
              <button className="btn pri sm" disabled={planning} onClick={() => planFrom("/documents/link-plan",
                { ids: [...picked.keys()] }, "把勾選的連在一起")}>{planning ? "配對中…" : "把勾選的連在一起"}</button>
            </div>
          )}
          {relink && (
            <div style={{ margin: "12px 14px", padding: "12px 14px", border: "1px solid var(--accent)", borderRadius: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
                <div><strong>改對應單據</strong>
                  <div className="small">銀行 {relink.bank.direction === "in" ? "入款" : "付款"} {money(relink.bank.amount)}
                    （{relink.doc.doc_date}，原本是「{relink.doc.counterparty?.name || relink.doc.memo || "單據"}」）。勾選要改對到的單據，例如 POS 匯入的信用卡、現金應收；
                    確認後原本{relink.doc.source_kind === "bank_only" ? "由銀行建立的這張會作廢" : "的結清會取消"}，差額照下方選擇處理。</div></div>
                <button className="btn sm" onClick={() => setRelink(null)}>取消</button>
              </div>
              <ManualMatch reviewId="" relinkDocId={relink.doc.id} preselect={[]}
                           bank={{ ...relink.bank, counterparty_account_no: null }}
                           onDone={(msg) => { toast(`已改對應：${msg}`); setRelink(null); refresh(); }} />
            </div>
          )}
          {canPay && !mixed && pickedDocs.some((d) => d.bank_txn_ids?.length) && (
            <UnmatchBar docs={[...picked.values()].filter((d) => d.bank_txn_ids?.length)}
                        onDone={() => { setPicked(new Map()); refresh(); }} />
          )}
          {canPay && picked.size > 0 && (
            <StoreBar docs={[...picked.values()]} onDone={() => { setPicked(new Map()); refresh(); }} />
          )}
          {canPay && !mixed && pickedDocs.some(payable) && (
            <CashBar docs={[...picked.values()].filter(payable)} type={type} onDone={() => { setPicked(new Map()); refresh(); }}
                     onClear={() => setPicked(new Map())} />
          )}
          {list.loading && !list.data && <Skeleton rows={8} />}
          {list.error && <div className="pad"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
          {list.data && items.length === 0 && (
            <Empty title={filtered ? "沒有符合的單據" : `${q.period} 沒有${TYPES[type]}單據`}
                   action={filtered ? <button className="btn" onClick={() => { setStatus(""); setSearch(""); setSource(""); q.set({ store: null }); }}>清除篩選</button> : undefined} />
          )}
          {items.length > 0 && (
            <div className="tbl-wrap">
              <table>
                <thead><tr>{canPay && <th style={{ width: 36 }}>
                  <input type="checkbox" aria-label="全選這一頁的單據" checked={allPicked} disabled={!pageOpen.length}
                         onChange={toggleAll} style={{ width: 18, minHeight: 18 }} /></th>}<th>對象</th><th>公司／門市</th><th>{type === "payable" ? "單據日期" : "營業／單據日期"}</th><th>發票</th><th>來源</th><th>到期日</th>
                  <th>{type === "payable" ? "付款日" : "入款日"}</th><th className="r">金額</th><th className="r">已結清</th><th className="r">未結清</th><th>狀態</th></tr></thead>
                <tbody>
                  {items.map((d) => {
                    const [label, cls] = settleLabel(d.settle_status, d.doc_type);
                    const overdue = d.due_date && d.due_date < today && (d.outstanding || 0) > 0;
                    return (
                      <tr key={d.id} className={`clickable ${docId === d.id ? "sel" : ""}`} onClick={() => setDocId(d.id)}>
                        {canPay && <td onClick={(e) => e.stopPropagation()}>
                          {selectable(d) && <input type="checkbox" aria-label={`勾選 ${d.counterparty?.name || "單據"}`} checked={picked.has(d.id)}
                                                onChange={() => toggle(d)} style={{ width: 18, minHeight: 18 }} />}</td>}
                        <td><div style={{ fontWeight: 500 }}>{d.counterparty?.name || "（未指定）"}</div>
                          <div className="small">{d.memo || d.period_keys.join("、")}{d.status === "draft" ? "・草稿" : ""}</div></td>
                        <td className="small">{q.company ? "" : <div>{companyName(d.company_id)}</div>}
                          <div>{d.store_ids.length ? d.store_ids.map((x) => storeName(x)).join("、") : "公司層級"}</div></td>
                        <td className="num small">{d.doc_date}</td>
                        <td className="num small">{d.invoice_nos.length ? d.invoice_nos.join(" ") : d.has_invoice ? "有" : "無發票"}</td>
                        <td><Tag kind="src">{SOURCE_LABEL[d.source_kind] || d.source_kind}</Tag>
                          {d.source_kind === "bank_only" && d.bank_txn_ids?.length === 1 && d.status !== "void" &&
                            <div><button className="btn sm" style={{ marginTop: 4, minHeight: 26, padding: "0 8px" }}
                                         onClick={(e) => { e.stopPropagation(); relinkRow(d); window.scrollTo({ top: 0, behavior: "smooth" }); }}>改對應</button></div>}</td>
                        <td className="num small">{d.due_date || "—"}</td>
                        <td className="num small">{d.settled_dates?.length
                          ? <>{d.settled_dates[d.settled_dates.length - 1]}{d.settled_dates.length > 1 && <div className="small">共 {d.settled_dates.length} 次</div>}</>
                          : "—"}</td>
                        <td className="num r">{money(d.settle_amount)}</td>
                        <td className="num r">{money(d.settled_amount)}</td>
                        <td className="num r">{money(d.outstanding)}</td>
                        <td><span className={`tag ${cls}`}>{label}</span>{overdue && <div className="small neg">已逾期</div>}</td>
                      </tr>
                    );
                  })}
                  {sm && <tr className="sum"><td colSpan={canPay ? 8 : 7}>全部 {sm.count} 筆合計</td><td className="num r">{money(sm.total)}</td>
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
        {docId && <DocumentDrawer id={docId} onClose={() => setDocId(null)} onChanged={refresh}
                                  onRelink={(t) => { setRelink(t); setDocId(null); window.scrollTo({ top: 0, behavior: "smooth" }); }} />}
      </div>
    </>
  );
}

const SOURCES = [["petty_cash", "零用金"], ["store_cash", "門市收銀"], ["advance", "老闆／員工代墊"]] as const;

/** 勾選後出現：付款日、來源、備註；一張時可改金額（少付、分次付）。同一次只能付同一家公司（含分公司）的單據。 */
function CashBar({ docs, type, onDone, onClear }: { docs: Doc[]; type: T; onDone: () => void; onClear: () => void }) {
  const toast = useToast();
  const { companyName } = useDirectory();
  const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
  const [date, setDate] = useState(today);
  const [source, setSource] = useState<string>("petty_cash");
  const [note, setNote] = useState("");
  const total = docs.reduce((a, d) => a + (d.outstanding || 0), 0);
  const [amount, setAmount] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const companies = [...new Set(docs.map((d) => companyName(d.company_id)))];
  const verb = type === "payable" ? "付" : "收";

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const r = await apiSend<{ total: number }>("POST", "/cash-payments", {
        document_ids: docs.map((d) => d.id), paid_date: date, source, note: note || undefined,
        amount: docs.length === 1 && amount ? Number(amount) : undefined });
      toast(`已標記現金${verb}款：${docs.length} 張，${money(r.total)}`);
      onDone();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  return (
    <div className="ai" style={{ margin: "12px 14px", display: "grid", gap: 10 }}>
      <div style={{ fontWeight: 600 }}>現金{verb}款：已勾選 {docs.length} 張，未結清合計 <span className="num">{money(total)}</span>
        <span className="small">（{companies.join("、")}）</span></div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label className="ctl"><span className="k">{verb}款日</span><input type="date" value={date} max={today} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="ctl"><span className="k">錢從哪裡出</span>
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            {SOURCES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        {docs.length === 1 && <label className="ctl"><span className="k">金額（空白＝付清）</span>
          <input inputMode="numeric" value={amount} placeholder={String(total)} onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))} style={{ width: 120 }} /></label>}
        <label className="ctl" style={{ flex: "1 1 200px" }}><span className="k">備註</span>
          <input value={note} maxLength={200} placeholder="例：大樂店收銀付、收據號碼、誰代墊" onChange={(e) => setNote(e.target.value)} /></label>
        <button className="btn pri" disabled={busy || !date} onClick={submit}>{busy ? "處理中…" : `標為已${verb}`}</button>
        <button className="btn" disabled={busy} onClick={onClear}>取消勾選</button>
      </div>
      <FieldErrors error={err} />
      <div className="small">只改已{verb}／未{verb}，不影響損益。標錯了：點單據，在右側「結清紀錄」按取消。</div>
    </div>
  );
}

/** 勾選的單據一次改門市或損益月份（例：薪資改到青年店；銀行交易選錯月份）。POST /documents/batch-store、batch-period */
function StoreBar({ docs, onDone }: { docs: Doc[]; onDone: () => void }) {
  const toast = useToast();
  const { dir } = useDirectory();
  const root = (cid: string) => {
    let c = dir.companies.find((x) => x.id === cid);
    while (c?.parent_company_id) c = dir.companies.find((x) => x.id === c!.parent_company_id) || undefined;
    return c?.id || cid;
  };
  const roots = new Set(docs.map((d) => root(d.company_id)));
  const stores = roots.size === 1 ? dir.stores.filter((st) => root(st.company_id) === [...roots][0]) : [];
  const [store, setStore] = useState("");
  const [month, setMonth] = useState("");
  const [busy, setBusy] = useState(false);
  async function goPeriod() {
    setBusy(true);
    try {
      const r = await apiSend<{ ok: string[]; failed: { message: string }[] }>("POST", "/documents/batch-period",
        { document_ids: docs.map((d) => d.id), period_key: month });
      toast(r.failed.length ? `改好 ${r.ok.length} 張，${r.failed.length} 張失敗：${r.failed[0].message}` : `已改到 ${month}：${r.ok.length} 張`,
            r.failed.length ? "error" : "ok");
      onDone();
    } catch (e) { toast((e as Error).message || "改月份失敗", "error"); } finally { setBusy(false); }
  }
  async function go() {
    setBusy(true);
    try {
      const r = await apiSend<{ ok: string[]; failed: { message: string }[] }>("POST", "/documents/batch-store",
        { document_ids: docs.map((d) => d.id), store_id: store });
      toast(r.failed.length ? `改好 ${r.ok.length} 張，${r.failed.length} 張失敗：${r.failed[0].message}` : `已改到${store ? "門市" : "公司層級"}：${r.ok.length} 張`,
            r.failed.length ? "error" : "ok");
      onDone();
    } catch (e) { toast((e as Error).message || "改門市失敗", "error"); } finally { setBusy(false); }
  }
  return (
    <div style={{ margin: "12px 14px 0", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
      <label className="ctl"><span className="k">改門市（勾選 {docs.length} 張）</span>
        <select value={store} onChange={(e) => setStore(e.target.value)} disabled={roots.size !== 1}>
          <option value="">公司層級（不分門市）</option>
          {stores.map((st) => <option key={st.id} value={st.id}>{st.code} {st.name}</option>)}
        </select></label>
      <button className="btn" disabled={busy || roots.size !== 1} onClick={go}>{busy ? "處理中…" : "改門市"}</button>
      {roots.size > 1 && <span className="small">勾選的跨不同公司，門市請分開改</span>}
      <label className="ctl" style={{ marginLeft: 12 }}><span className="k">改損益月份</span>
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></label>
      <button className="btn" disabled={busy || !month} onClick={goPeriod}>{busy ? "處理中…" : "改月份"}</button>
    </div>
  );
}

/** 勾選含銀行核對的單據：取消核對，銀行交易送回「待確認」（由銀行建立的單據作廢、被結清的單據回到未收／未付）。 */
function UnmatchBar({ docs, onDone }: { docs: Doc[]; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);
  const made = docs.filter((d) => d.source_kind === "bank_only").length;
  async function go() {
    setBusy(true);
    try {
      let reopened = 0; const failed: { id: string; message: string }[] = [];
      for (let i = 0; i < docs.length; i += 5) {   // 分批送，避免超過連線時間
        const r = await apiSend<{ reopened: number; failed: { id: string; message: string }[] }>("POST", "/documents/batch-unmatch",
          { ids: docs.slice(i, i + 5).map((d) => d.id) });
        reopened += r.reopened; failed.push(...r.failed);
      }
      toast(`已送回待確認 ${reopened} 筆銀行交易${failed.length ? `；${failed.length} 張沒處理：${failed[0].message}` : ""}`,
            failed.length ? "error" : undefined);
      onDone();
    } catch (e) { toast((e as Error).message || "失敗", "error"); } finally { setBusy(false); setSure(false); }
  }
  return (
    <div className="ai" style={{ margin: "12px 14px", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
      <span className="small">勾選中有 {docs.length} 張由銀行交易核對{made ? `（其中 ${made} 張是銀行直接建立的）` : ""}。
        金額跟 POS 重複、或對錯單據時，可以取消核對，銀行交易回到「待確認」重新對應。</span>
      {!sure ? <button className="btn sm" disabled={busy} onClick={() => setSure(true)}>取消核對，送回待確認</button>
        : <><button className="btn sm pri" disabled={busy} onClick={go}>{busy ? "處理中…" : `確定送回 ${docs.length} 張`}</button>
            <button className="btn sm" disabled={busy} onClick={() => setSure(false)}>先不要</button></>}
    </div>
  );
}
