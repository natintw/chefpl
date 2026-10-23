"use client";
/** S8 待確認：系統判斷不了、需要人決定的帳。資料：GET /review-items、POST /review-items/{id}/resolve。 */
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, FieldErrors, Skeleton, Tag, useToast } from "@/components/ui";
import { ApiError, apiSend, useApi } from "@/lib/api";
import { money, REVIEW_LABEL } from "@/lib/format";
import { ManualMatch } from "@/components/ManualMatch";
import type { ReviewItem } from "@/lib/types";
import Link from "next/link";

export default function ReviewPage() {
  const q = useQuery();
  const list = useApi<{ items: ReviewItem[] }>("/review-items", { company_id: q.company || undefined });
  const [kind, setKind] = useState("");
  const [done, setDone] = useState<Set<string>>(new Set());
  const items = (list.data?.items || []).filter((i) => !done.has(i.id));
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach((i) => m.set(i.kind, (m.get(i.kind) || 0) + 1));
    return [...m.entries()];
  }, [items]);
  const shown = items.filter((i) => !kind || i.kind === kind);
  const newCps = items.filter((i) => i.kind === "new_counterparty");
  const toast = useToast();
  const [bulk, setBulk] = useState(false);
  async function ackAllNew() {
    setBulk(true);
    const ok: string[] = [];
    for (const it of newCps) {
      try { await apiSend("POST", `/review-items/${it.id}/resolve`, { action: "acknowledge", payload: {} }); ok.push(it.id); }
      catch { /* 失敗的留在清單上 */ }
    }
    setDone((s) => new Set([...s, ...ok])); setBulk(false);
    toast(ok.length === newCps.length ? `已確認 ${ok.length} 個新對象` : `確認 ${ok.length} 個，${newCps.length - ok.length} 個失敗`, ok.length === newCps.length ? "ok" : "error");
  }

  return (
    <>
      <PageHeader crumb="作業" title={<>待確認 <span className="num sub" style={{ fontSize: 18 }}>{items.length}</span></>}
                  sub="處理完的會從清單移除；月結前要清空。" />
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      {list.loading && !list.data && <div className="card"><Skeleton rows={6} /></div>}
      {list.data && items.length > 0 && (
        <div role="group" aria-label="依類型篩選" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          <button className="chip" aria-pressed={!kind} onClick={() => setKind("")}>全部 <span className="num">{items.length}</span></button>
          {counts.map(([k, n]) => <button key={k} className="chip" aria-pressed={kind === k} onClick={() => setKind(k)}>{REVIEW_LABEL[k] || k} <span className="num">{n}</span></button>)}
          {newCps.length > 1 && (!kind || kind === "new_counterparty") && (
            <button className="btn sm" style={{ marginLeft: "auto" }} disabled={bulk} onClick={ackAllNew}>
              {bulk ? "確認中…" : `新對象 ${newCps.length} 個全部確認（不設預設類別）`}</button>
          )}
        </div>
      )}
      {list.data && items.length === 0 && (
        <div className="card"><Empty title="沒有待確認的項目" action={<Link className="btn pri" href={q.keep("/close")}>前往月結</Link>}>
          這個月的帳都有人確認過了，可以去月結。</Empty></div>
      )}
      <div className="stack" style={{ maxWidth: 960 }}>
        {shown.map((it) => <Card key={it.id} it={it} onDone={() => setDone((s) => new Set(s).add(it.id))} />)}
      </div>
    </>
  );
}

function Card({ it, onDone }: { it: ReviewItem; onDone: () => void }) {
  const toast = useToast();
  const { companyName, storeName, categories, categoryName } = useDirectory();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const p = it.payload;
  const bank = p.item as Record<string, any> | undefined;
  const [cat, setCat] = useState<string>(bank?.suggested_category_code || p.suggestion?.category_code || "");
  const [cpName, setCpName] = useState<string>(bank?.counterparty_name || "");
  const [remember, setRemember] = useState(true);
  const [manual, setManual] = useState(false);

  async function resolve(action: string, payload: Record<string, unknown> = {}, msg = "已處理") {
    setBusy(true); setErr(null);
    try {
      await apiSend("POST", `/review-items/${it.id}/resolve`, { action, payload });
      toast(msg); onDone();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  const tagKind = it.kind === "intercompany_mismatch" ? "neg" : ["duplicate_source", "new_counterparty"].includes(it.kind) ? "est" : "warn";
  const catSelect = (natures: string[]) => (
    <label className="ctl"><span className="k">類別</span>
      <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="類別">
        <option value="">選擇類別</option>
        {categories.filter((c) => natures.includes(c.nature)).map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</option>)}
      </select></label>
  );

  return (
    <article className="card pad stack" aria-label={REVIEW_LABEL[it.kind] || it.kind}>
      <div><Tag kind={tagKind as never}>{REVIEW_LABEL[it.kind] || it.kind}</Tag> <span className="small">{companyName(it.company_id)}{it.period ? `・${it.period}` : ""}</span>
        <h2 style={{ marginTop: 6 }}>{title(it)}</h2></div>

      {bank && (
        <div className="kv">
          <div><div className="k">交易日</div><div className="num">{bank.txn_date}</div></div>
          <div><div className="k">帳戶</div><div>{bank.cash_account_id}</div></div>
          <div><div className="k">方向</div><div>{bank.direction === "out" ? "支出" : "收入"}</div></div>
          {bank.bank_note && <div><div className="k">備註</div><div>{bank.bank_note}</div></div>}
          {bank.counterparty_name && <div><div className="k">對方戶名</div><div>{bank.counterparty_name}</div></div>}
        </div>
      )}
      {it.kind === "bank_only" && p.reason && <p className="small" style={{ margin: 0 }}>系統比對結果：{p.reason}</p>}
      {it.kind === "bank_only" && p.suggestion?.documents?.length > 0 && (
        <div className="ai">
          <div style={{ fontWeight: 600 }}>可能是：{p.suggestion.counterparty_name || "以下單據"}，{p.suggestion.documents.length} 張未付合計
            <span className="num"> {money(p.suggestion.documents.reduce((a: number, d: { amount: number }) => a + d.amount, 0))}</span></div>
          <div className="small">
            {p.suggestion.documents.map((d: { document_id: string; amount: number; store_ids: string[]; period: string }) =>
              `${d.period} ${d.store_ids.map((x) => storeName(x)).join("、") || "公司層級"} ${money(d.amount)}`).join("；")}
          </div>
        </div>
      )}
      {it.kind === "unclassified" && p.suggestion && (
        <div className="ai"><div style={{ fontWeight: 600 }}>建議：{p.suggestion.category_code} {categoryName(p.suggestion.category_code)} <Tag kind="src">信心 {p.suggestion.confidence}</Tag></div>
          <div>{p.suggestion.reason}</div><div className="small">AI 建議，採用前不會入帳（{p.suggestion.model_version}）</div></div>
      )}
      {it.kind === "duplicate_source" && (
        <div className="kv"><div><div className="k">發票</div><div className="num">{p.invoice?.invoice_no}</div></div>
          <div><div className="k">對象</div><div>{p.invoice?.seller_name}</div></div>
          <div><div className="k">金額</div><div className="num">{money(p.invoice?.total_amount)}</div></div></div>
      )}

      <FieldErrors error={err} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {it.kind === "bank_only" && p.suggestion?.documents?.length > 0 && (
          <button className="btn pri" disabled={busy} onClick={() => resolve("settle_suggestion", {},
            `已標為${bank?.direction === "out" ? "已付" : "已收"}：${p.suggestion.documents.length} 張`)}>
            是這幾張，標為{bank?.direction === "out" ? "已付" : "已收"}</button>
        )}
        {it.kind === "bank_only" && <>
          {catSelect(bank?.direction === "out" ? ["expense", "balance"] : ["income", "balance"])}
          <label className="ctl"><span className="k">對象</span><input value={cpName} onChange={(e) => setCpName(e.target.value)} placeholder="例：臺灣銀行" /></label>
          <button className="btn pri" disabled={busy || !cat} onClick={() => resolve("create_document", { category_code: cat, counterparty_name: cpName || undefined },
            `已建立已結清單據：${categoryName(cat)} ${money(bank?.amount)}`)}>建立已結清單據</button>
        </>}
        {it.kind === "unclassified" && <>
          {catSelect(["expense", "income", "balance"])}
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} style={{ width: 18, minHeight: 18 }} /> 記住這家廠商的類別</label>
          <button className="btn pri" disabled={busy || !cat} onClick={() => resolve("set_category", { category_code: cat, remember_rule: remember }, "已入帳")}>確認類別並入帳</button>
        </>}
        {it.kind === "amount_only_match" && <button className="btn pri" disabled={busy} onClick={() => resolve("confirm_match", {}, "已標為結清")}>是這筆，標為已結清</button>}
        {it.kind === "duplicate_source" && <>
          <button className="btn pri" disabled={busy} onClick={() => resolve("link", {}, "已視為同一筆")}>視為同一筆</button>
          <button className="btn" disabled={busy} onClick={() => resolve("keep_both", {}, "已另建一筆")}>不是同一筆，兩筆都保留</button>
        </>}
        {it.kind === "new_counterparty" && <>
          {catSelect(["expense", "income", "balance"])}
          <button className="btn pri" disabled={busy || !cat} onClick={() => resolve("set_default_category", { category_code: cat }, "已設定預設類別")}>設為預設類別</button>
          <button className="btn" disabled={busy} onClick={() => resolve("acknowledge", {}, "已確認")}>只確認</button>
        </>}
        {!["bank_only", "unclassified", "amount_only_match", "duplicate_source", "new_counterparty"].includes(it.kind) &&
          <button className="btn pri" disabled={busy} onClick={() => resolve("acknowledge", {}, "已確認")}>確認</button>}
        {it.kind === "bank_only" && bank?.company_id &&
          <button className="btn" disabled={busy} aria-expanded={manual} onClick={() => setManual(!manual)}>
            {manual ? "收起手動核對" : "手動核對（金額有差、帳號不對）"}</button>}
        <button className="btn" disabled={busy} onClick={() => resolve("dismiss", {}, "已略過")}>略過</button>
      </div>
      {it.kind === "bank_only" && manual && bank?.company_id && (
        <ManualMatch reviewId={it.id} bank={bank as never}
                     preselect={(p.suggestion?.documents || []).map((d: { document_id: string }) => d.document_id)}
                     onDone={(msg) => { toast(msg); onDone(); }} />
      )}
    </article>
  );
}

function title(it: ReviewItem): string {
  const p = it.payload;
  switch (it.kind) {
    case "bank_only": return `${p.item.cash_account_id} ${p.item.direction === "out" ? "支出" : "收入"} ${money(p.item.amount)}，摘要「${p.item.bank_memo}」`;
    case "unclassified": return `${p.counterparty || "（未指定對象）"}：分類規則沒有命中`;
    case "amount_only_match": return `銀行交易 ${money(p.item?.amount)} 只有金額相符，看不出付給誰`;
    case "duplicate_source": return "發票系統送來的發票，應付表裡已經有了";
    case "new_counterparty": return `新對象「${p.name}」${p.tax_id ? `（${p.tax_id}）` : ""}`;
    case "b99_open": return "有一筆歸在 B99 其他，月結前要改歸";
    case "recurring_ending": return `固定費用「${p.description}」將於 ${p.end_period} 結束`;
    default: return REVIEW_LABEL[it.kind] || it.kind;
  }
}
