"use client";
/** S1 損益總覽（Bill 首頁）。資料：GET /pl/overview。 */
import Link from "next/link";
import { useMemo } from "react";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, useQuery } from "@/components/filters";
import { Empty, ErrorBox, KpiSkeleton, Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import { addMonths, GROUP_NAME, money, pct, PRIME_WARN } from "@/lib/format";
import type { CompanySubtotal, Overview, OverviewRow } from "@/lib/types";

export default function OverviewPage() {
  const q = useQuery();
  const cur = useApi<Overview>("/pl/overview", { period: q.period });
  const prev = useApi<Overview>("/pl/overview", { period: addMonths(q.period, -1) });

  const groups = useMemo(() => {
    const m = new Map<string, { name: string; rows: OverviewRow[]; sub?: CompanySubtotal }>();
    (cur.data?.rows || []).forEach((r) => {
      if (!m.has(r.company.id)) m.set(r.company.id, { name: r.company.name, rows: [] });
      m.get(r.company.id)!.rows.push(r);
    });
    (cur.data?.companies || []).forEach((c) => {
      if (!m.has(c.id)) m.set(c.id, { name: c.name, rows: [] });
      m.get(c.id)!.sub = c;
    });
    return [...m.entries()];
  }, [cur.data]);

  const d = cur.data;
  const partial = d?.rows.filter((r) => r.completeness.level === "partial") || [];
  const missingAll = [...new Set(partial.flatMap((r) => r.completeness.missing))];
  const revDelta = d && prev.data?.totals.revenue ? ((d.totals.revenue - prev.data.totals.revenue) / prev.data.totals.revenue) * 100 : null;
  const notes = attention(d);

  return (
    <>
      <PageHeader crumb="損益" title="損益總覽" sub={`${q.period}・${GROUP_NAME}集團`}>
        <PeriodPicker />
      </PageHeader>

      {cur.error && <ErrorBox error={cur.error} onRetry={cur.reload} />}
      {cur.loading && !d && <><KpiSkeleton /><div className="card"><Skeleton rows={8} /></div></>}

      {d && d.stores_with_data === 0 && (
        <div className="card">
          <Empty title={`${q.period} 還沒有任何帳務資料`}
                 action={<Link className="btn pri" href={q.keep("/imports")}>從匯入應付帳款表開始</Link>}>
            匯入一份現有的廠商應付帳款表，系統會依分類規則自動分好類，接著再匯入薪資表和營收。
          </Empty>
        </div>
      )}

      {d && d.stores_with_data > 0 && (
        <>
          {(missingAll.length > 0 || d.unallocated_amount !== 0) && (
            <section className="band" aria-label="資料完整度">
              <Tag kind="warn">暫估</Tag>
              <span style={{ flex: "1 1 300px" }}>{q.period} 資料還沒齊，標「暫估」的數字之後可能變動。</span>
              <span style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {missingAll.slice(0, 4).map((m) => <Tag key={m}>{m}</Tag>)}
                {d.unallocated_amount !== 0 && <Tag>未分攤 <span className="num">{money(d.unallocated_amount)}</span></Tag>}
                {d.accrual_amount > 0 && <Tag>含預估 <span className="num">{money(d.accrual_amount)}</span></Tag>}
              </span>
              <Link href={q.keep("/todo")} style={{ fontWeight: 600, fontSize: 13 }}>看還缺什麼</Link>
            </section>
          )}

          <section className="grid-kpi" aria-label="關鍵指標">
            <Kpi label="集團營收（未稅）" value={money(d.totals.revenue)}
                 foot={<>{revDelta !== null && <>較上月 <span className={`num ${revDelta >= 0 ? "pos" : "neg"}`}>{revDelta >= 0 ? "+" : ""}{revDelta.toFixed(1)}%</span>・</>}集團內已沖銷</>} />
            <Kpi label="門市營業利益" value={money(d.totals.store_operating_profit)}
                 foot={<>利潤率 <span className="num">{pct(d.totals.store_margin_pct)}</span>・淨利 <span className="num">{money(d.totals.net_profit)}</span></>} />
            <Kpi label="Food cost" value={pct(d.totals.food_cost_pct)} foot="含集團內進貨；以進貨計" />
            <Kpi label="Prime cost" alert={(d.totals.prime_cost_pct ?? 0) > PRIME_WARN}
                 value={<>{pct(d.totals.prime_cost_pct)}{(d.totals.prime_cost_pct ?? 0) > PRIME_WARN && <span style={{ fontSize: 14 }}> ▲</span>}</>}
                 foot={<>{(d.totals.prime_cost_pct ?? 0) > PRIME_WARN ? `高於警戒 ${PRIME_WARN}%・` : ""}Labor <span className="num">{pct(d.totals.labor_cost_pct)}</span></>} />
          </section>

          <div className="row">
            <section className="card grow" aria-label="各公司與門市">
              <div className="card-h"><h2>各門市 {q.period}</h2><Link href={q.keep("/pl")} className="small">看完整損益表</Link></div>
              <div className="tbl-wrap">
                <table>
                  <thead><tr><th>公司／門市</th><th className="r">營收</th><th className="r">Food cost</th><th className="r">Labor</th>
                    <th className="r">Prime</th><th className="r">營業利益</th><th className="r">利潤率</th><th>資料</th></tr></thead>
                  <tbody>
                    {groups.map(([cid, g]) => (
                      <GroupRows key={cid} name={g.name} rows={g.rows} sub={g.sub}
                                 companyHref={q.link("/pl", { company: cid, store: null })}
                                 unallocHref={q.link("/pl", { company: cid, store: "unallocated" })}
                                 href={(r) => q.link("/pl", { company: r.company.id, store: r.store.id })} />
                    ))}
                    {!d.companies && d.unallocated_amount !== 0 && (
                      <tr><td className="sub">公司層級（未分攤）</td><td className="r">—</td><td className="r">—</td><td className="r">—</td><td className="r">—</td>
                        <td className="num r neg">{money(-d.unallocated_amount)}</td><td className="r">—</td><td><Tag kind="warn">未分攤</Tag></td></tr>
                    )}
                    <tr className="sum"><td>{GROUP_NAME} 合計</td><td className="num r">{money(d.totals.revenue)}</td><td className="num r">{pct(d.totals.food_cost_pct)}</td>
                      <td className="num r">{pct(d.totals.labor_cost_pct)}</td>
                      <td className={`num r ${(d.totals.prime_cost_pct ?? 0) > PRIME_WARN ? "warnv" : ""}`}>{pct(d.totals.prime_cost_pct)}</td>
                      <td className="num r">{money(d.totals.store_operating_profit)}</td><td className="num r">{pct(d.totals.store_margin_pct)}</td>
                      <td>{partial.length ? <Tag>暫估</Tag> : <Tag kind="ok">齊全</Tag>}</td></tr>
                  </tbody>
                </table>
              </div>
              <p className="small" style={{ margin: 0, padding: "12px 16px" }}>金額為未稅。門市列的營業利益是四牆利潤；公司列是旗下門市加上公司層級費用，集團合計再沖銷集團內交易。Food cost、Labor、Prime 以營收計算；工廠另看製造成本。</p>
            </section>

            <aside className="card aside pad" aria-label="需要注意">
              <h2 style={{ marginBottom: 12 }}>需要注意</h2>
              {notes.length === 0 ? <p className="sub">目前沒有需要注意的項目。</p> : (
                <div className="stack" style={{ gap: 14 }}>
                  {notes.map((n, i) => (
                    <div key={i} style={{ display: "flex", gap: 10 }}>
                      <span><Tag kind={n.kind}>{n.tag}</Tag></span>
                      <div><div style={{ fontWeight: 600 }}>{n.title}</div><div className="sub">{n.body}</div></div>
                    </div>
                  ))}
                </div>
              )}
            </aside>
          </div>
        </>
      )}
    </>
  );
}

function Kpi({ label, value, foot, alert }: { label: string; value: React.ReactNode; foot: React.ReactNode; alert?: boolean }) {
  return (
    <div className={`card pad kpi ${alert ? "alert" : ""}`}>
      <div className="k">{label}</div>
      <div className={`v ${alert ? "warnv" : ""}`}>{value}</div>
      <div className="small">{foot}</div>
    </div>
  );
}

function GroupRows({ name, rows, sub, href, companyHref, unallocHref }: { name: string; rows: OverviewRow[]; sub?: CompanySubtotal;
  href: (r: OverviewRow) => string; companyHref: string; unallocHref: string }) {
  const hot = (sub?.prime_cost_pct ?? 0) > PRIME_WARN;
  return (
    <>
      {sub ? (
        <tr className="co">
          <td><Link href={companyHref} style={{ color: "var(--text)" }}>{sub.parent_company_id ? "└ " : ""}{name}</Link></td>
          <td className="num r">{money(sub.revenue)}</td>
          <td className="num r">{pct(sub.food_cost_pct)}</td>
          <td className="num r">{pct(sub.labor_cost_pct)}</td>
          <td className={`num r ${hot ? "warnv" : ""}`}>{pct(sub.prime_cost_pct)}</td>
          <td className="num r">{money(sub.operating_profit)}</td>
          <td className="num r">{pct(sub.store_margin_pct)}</td>
          <td className="small">公司小計</td>
        </tr>
      ) : <tr className="co"><td colSpan={8}>{name}</td></tr>}
      {rows.map((r) => {
        const est = r.completeness.level === "partial";
        const hot = (r.prime_cost_pct ?? 0) > PRIME_WARN;
        const cls = `num r ${est ? "est" : ""}`;
        return (
          <tr key={r.store.id}>
            <td><Link href={href(r)} style={{ color: "var(--text)", fontWeight: 500 }}>{r.store.name}</Link></td>
            <td className={cls}>{money(r.revenue)}</td>
            <td className={cls}>{pct(r.food_cost_pct)}</td>
            <td className={cls}>{pct(r.labor_cost_pct)}</td>
            <td className={`${cls} ${hot && !est ? "warnv" : ""}`}>{pct(r.prime_cost_pct)}{hot ? " ▲" : ""}</td>
            <td className={cls}>{money(r.store_operating_profit)}</td>
            <td className={cls}>{pct(r.store_margin_pct)}</td>
            <td>{est ? <Tag>暫估・{r.completeness.missing[0]}</Tag> : <Tag kind="ok">齊全</Tag>}</td>
          </tr>
        );
      })}
      {sub && sub.unallocated_amount !== 0 && (
        <tr>
          <td><Link href={unallocHref} className="sub">公司層級（未分攤）</Link></td>
          <td className="r">—</td><td className="r">—</td><td className="r">—</td><td className="r">—</td>
          <td className="num r neg">{money(-sub.unallocated_amount)}</td><td className="r">—</td>
          <td><Tag kind="warn">未分攤</Tag></td>
        </tr>
      )}
    </>
  );
}

function attention(d: Overview | null) {
  if (!d) return [];
  const out: { tag: string; kind: "warn" | "neg" | "est"; title: string; body: string }[] = [];
  d.rows.filter((r) => (r.prime_cost_pct ?? 0) > PRIME_WARN).forEach((r) => out.push({
    tag: "KPI", kind: "warn", title: `${r.store.name} Prime cost ${pct(r.prime_cost_pct)}`,
    body: r.completeness.level === "partial" ? `資料未齊（${r.completeness.missing.join("、")}），比例可能偏高。` : `高於警戒 ${PRIME_WARN}%。`,
  }));
  if (d.intercompany_unmatched) out.push({ tag: "集團內", kind: "neg", title: `集團內銷貨與進貨差 ${money(Math.abs(d.intercompany_unmatched))}`,
    body: "兩邊公司記的金額不一致，月結前要核對。" });
  if (d.unallocated_amount) out.push({ tag: "分攤", kind: "warn", title: `公司層級 ${money(d.unallocated_amount)} 尚未分到門市`,
    body: "各店四牆利潤目前未扣總部費用。" });
  const pending = d.rows.reduce((a, r) => a + (r.completeness.pending_invoices || 0), 0);
  if (pending) out.push({ tag: "發票", kind: "est", title: `發票系統待複核 ${pending} 張`, body: "確認後會自動進應付。" });
  if (d.rows.some((r) => !r.completeness.invoice_system_reachable)) out.push({ tag: "上游", kind: "warn",
    title: "暫時讀不到發票系統", body: "發票來源的數字可能不是最新。" });
  if (d.advance_balance) out.push({ tag: "預收", kind: "est", title: `預收款尚未轉營收 ${money(d.advance_balance)}`,
    body: "欠客人的貨，交貨後才算營收。" });
  return out;
}
