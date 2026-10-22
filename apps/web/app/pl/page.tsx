"use client";
/** S2 損益表：依月份比較或依門市並列；點數字下鑽到明細與單據。資料：/pl/statement、/pl/drilldown。 */
import { useMemo, useState } from "react";
import { DocumentDrawer } from "@/components/DocumentDrawer";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, ScopePicker, useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import { addMonths, delta, money, pct, PRIME_WARN, SOURCE_LABEL } from "@/lib/format";
import type { Statement } from "@/lib/types";

type Row = { cls: string; label: string; code?: string; values: Record<string, number> };
type Drill = { category_code: string; label: string; col: string };

export default function PLPage() {
  const q = useQuery();
  const { storeName, companyName } = useDirectory();
  const mode = q.get("cols") === "store" ? "store" : "month";
  const prev = addMonths(q.period, -1);
  const params = mode === "month"
    ? { period_from: prev, period_to: q.period, columns: "month", company_id: q.company || undefined, store_id: q.store || undefined }
    : { period_from: q.period, period_to: q.period, columns: "store", company_id: q.company || undefined, store_id: q.store || undefined };
  const st = useApi<Statement>("/pl/statement", params);
  const [drill, setDrill] = useState<Drill | null>(null);
  const [docId, setDocId] = useState<string | null>(null);

  const rows = useMemo(() => (st.data ? buildRows(st.data) : []), [st.data]);
  const cols = st.data?.columns || [];
  const revenueOf = (k: string) => st.data?.sections.find((s) => s.section === "revenue")?.subtotal[k] || 0;
  const isEmpty = st.data && rows.every((r) => Object.values(r.values).every((v) => !v));
  const scopeTitle = q.store ? (q.store === "unallocated" ? "公司層級（未分攤）" : storeName(q.store)) : q.company ? companyName(q.company) : "集團";

  return (
    <>
      <PageHeader crumb="損益" title={`${scopeTitle} 損益表`} sub={mode === "month" ? `${q.period} 與 ${prev} 比較・金額未稅` : `${q.period}・依門市並列・金額未稅`}>
        <PeriodPicker />
        <ScopePicker />
        <div className="seg" role="group" aria-label="欄位">
          <button aria-pressed={mode === "month"} onClick={() => q.set({ cols: null })}>依月份</button>
          <button aria-pressed={mode === "store"} onClick={() => q.set({ cols: "store" })}>依門市</button>
        </div>
      </PageHeader>

      {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
      {st.data?.intercompany_eliminated && (st.data.intercompany_eliminated.revenue > 0) && (
        <p className="sub" style={{ marginTop: 0 }}>集團層級已沖銷集團內銷貨 <span className="num">{money(st.data.intercompany_eliminated.revenue)}</span>
          {st.data.intercompany_unmatched ? <>，兩邊差額 <span className="num neg">{money(st.data.intercompany_unmatched)}</span> 待核對</> : null}。</p>
      )}

      <div className="row">
        <section className="card grow" aria-label="損益表">
          {st.loading && !st.data && <Skeleton rows={14} />}
          {isEmpty && <Empty title="這個範圍沒有任何收入或支出">換個月份或範圍看看。</Empty>}
          {st.data && !isEmpty && (
            <div className="tbl-wrap">
              <table>
                <thead>
                  {mode === "month" ? (
                    <tr><th style={{ minWidth: 200 }}>項目</th><th className="r">{q.period}</th><th className="r">佔營收</th><th className="r">{prev}</th><th className="r">增減</th></tr>
                  ) : (
                    <tr><th style={{ minWidth: 200, position: "sticky", left: 0, background: "var(--surface)" }}>項目</th>
                      {cols.map((c) => <th key={c.key} className="r">{c.label}</th>)}</tr>
                  )}
                </thead>
                <tbody>
                  {rows.map((r, i) => {
                    if (mode === "month") {
                      const cur = r.values[q.period] || 0, before = r.values[prev] || 0;
                      const share = revenueOf(q.period) ? (cur / revenueOf(q.period)) * 100 : null;
                      const on = drill && r.code === drill.category_code;
                      return (
                        <tr key={i} className={`${r.cls} ${on ? "sel" : ""}`}>
                          <td>{r.label}</td>
                          <td className="num r">{r.cls === "kpi" ? kpiCell(r, q.period) : r.code && cur ? <button className="linkbtn num" onClick={() => { setDocId(null); setDrill({ category_code: r.code!, label: r.label, col: q.period }); }}>{money(cur)}</button> : money(cur)}</td>
                          <td className="num r sub">{r.cls === "kpi" ? "" : share === null ? "—" : `${share.toFixed(1)}%`}</td>
                          <td className="num r sub">{r.cls === "kpi" ? kpiCell(r, prev) : money(before)}</td>
                          <td className="num r">{r.cls === "kpi" ? kpiDelta(r, q.period, prev) : delta(cur - before)}</td>
                        </tr>
                      );
                    }
                    return (
                      <tr key={i} className={r.cls}>
                        <td style={{ position: "sticky", left: 0, background: "inherit" }}>{r.label}</td>
                        {cols.map((c) => (
                          <td key={c.key} className="num r">
                            {r.cls === "kpi" ? kpiCell(r, c.key)
                              : r.code && r.values[c.key] && c.key !== "total"
                                ? <button className="linkbtn num" onClick={() => { setDocId(null); setDrill({ category_code: r.code!, label: r.label, col: c.key }); }}>{money(r.values[c.key])}</button>
                                : money(r.values[c.key] || 0)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="small" style={{ margin: 0, padding: "12px 16px" }}>點有底線的數字可看明細。總部費用尚未分攤前，門市淨利等於四牆利潤。銷貨成本以當月進貨計。</p>
        </section>

        {drill && !docId && <DrillPanel drill={drill} mode={mode} onClose={() => setDrill(null)} onOpen={setDocId} />}
        {docId && <DocumentDrawer id={docId} onClose={() => setDocId(null)} />}
      </div>
    </>
  );
}

function DrillPanel({ drill, mode, onClose, onOpen }: { drill: Drill; mode: string; onClose: () => void; onOpen: (id: string) => void }) {
  const q = useQuery();
  const { storeName } = useDirectory();
  const store = mode === "store" ? (drill.col.startsWith("__company__:") ? "unallocated" : drill.col) : q.store || undefined;
  const company = mode === "store" && drill.col.startsWith("__company__:") ? drill.col.split(":")[1] : q.company || undefined;
  const period = mode === "store" ? q.period : drill.col;
  const dd = useApi<{ items: { document_id: string; doc_date: string; counterparty_name: string | null; description: string | null;
    memo: string | null; amount: number; source_kind: string; store_id: string | null }[] }>("/pl/drilldown",
    { period, company_id: company, store_id: store, category_code: drill.category_code });
  const total = dd.data?.items.reduce((a, b) => a + b.amount, 0) || 0;
  return (
    <aside className="card aside drawer" aria-label={`明細：${drill.label}`}>
      <div className="card-h">
        <div><div className="crumb">{period}・{store ? storeName(store === "unallocated" ? null : store) : "全部門市"}</div>
          <h2 style={{ fontSize: 16 }}>{drill.label} <span className="num">{money(total)}</span></h2></div>
        <button className="btn sm" onClick={onClose} aria-label="關閉明細">✕</button>
      </div>
      {dd.loading && <Skeleton rows={5} />}
      {dd.error && <div className="pad"><ErrorBox error={dd.error} onRetry={dd.reload} /></div>}
      {dd.data && dd.data.items.length === 0 && <Empty title="沒有明細" />}
      {dd.data && dd.data.items.length > 0 && (
        <table>
          <thead><tr><th>日期／對象</th><th>來源</th><th className="r">未稅</th></tr></thead>
          <tbody>
            {dd.data.items.map((it, i) => (
              <tr key={i} className="clickable" onClick={() => onOpen(it.document_id)}>
                <td><div className="small num">{it.doc_date}</div><div>{it.counterparty_name || "（未指定）"}</div>
                  <div className="small">{it.description || it.memo}</div></td>
                <td><Tag kind="src">{SOURCE_LABEL[it.source_kind] || it.source_kind}</Tag></td>
                <td className="num r"><button className="linkbtn num">{money(it.amount)}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  );
}

function buildRows(st: Statement): Row[] {
  const out: Row[] = [];
  const sec = (s: string) => st.sections.find((x) => x.section === s)!;
  const push = (s: string) => {
    const x = sec(s);
    if (!x) return;
    out.push({ cls: "sec", label: x.label, values: x.subtotal });
    x.lines.forEach((l) => out.push({ cls: "line", label: l.label, code: l.category_code, values: l.values }));
  };
  push("revenue"); push("cogs");
  out.push({ cls: "subt", label: "毛利", values: st.subtotals.gross_profit });
  ["labor", "occupancy", "opex", "channel"].forEach(push);
  out.push({ cls: "big", label: "門市營業利益（四牆利潤）", values: st.subtotals.store_operating_profit });
  if (sec("allocation").lines.length) push("allocation");
  out.push({ cls: "subt", label: "門市淨利", values: st.subtotals.store_net_profit });
  if (sec("non_operating").lines.length) {
    push("non_operating");
    out.push({ cls: "subt", label: "本期淨利", values: st.subtotals.net_profit });
  }
  (["food_cost_pct", "labor_cost_pct", "prime_cost_pct"] as const).forEach((k) => {
    const label = { food_cost_pct: "Food cost", labor_cost_pct: "Labor cost", prime_cost_pct: "Prime cost" }[k];
    const values: Record<string, number> = {};
    Object.entries(st.kpis).forEach(([col, v]) => { if (v[k] !== null) values[col] = v[k] as number; });
    out.push({ cls: "kpi", label, code: k, values });
  });
  return out;
}

function kpiCell(r: Row, col: string) {
  const v = r.values[col];
  if (v === undefined) return "—";
  const hot = r.code === "prime_cost_pct" && v > PRIME_WARN;
  return <span className={hot ? "warnv" : ""}>{pct(v)}{hot ? " ▲" : ""}</span>;
}
function kpiDelta(r: Row, a: string, b: string) {
  const x = r.values[a], y = r.values[b];
  if (x === undefined || y === undefined) return "—";
  const d = x - y;
  return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)} pt`;
}
