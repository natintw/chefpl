"use client";
/**
 * 待確認的銀行交易：手動挑單據核對。
 * 金額可以有些許差距（匯費、抹零、手續費被先扣、分次付），差額選「留著未付」或「記一張單據」；
 * 支出時會記住對方帳號（原本記在別家廠商的會改過來），下次自動對上。
 * 資料：GET /documents（同一法人、未結清）；送出：POST /review-items/{id}/resolve action=manual_match。
 */
import { useEffect, useMemo, useState } from "react";
import { useDirectory } from "./directory";
import { FieldErrors } from "./ui";
import { ApiError, apiSend, useApi } from "@/lib/api";
import { money } from "@/lib/format";
import type { Doc } from "@/lib/types";

type Bank = { cash_txn_id: string; company_id: string; direction: "out" | "in"; amount: number; counterparty_account_no?: string | null };

export function ManualMatch({ reviewId, bank, preselect, onDone }: {
  reviewId: string; bank: Bank; preselect: string[]; onDone: (msg: string) => void;
}) {
  const { dir, companyName, storeName, categories } = useDirectory();
  // 同一法人：總公司（含分公司）
  const root = useMemo(() => {
    let cur = dir.companies.find((c) => c.id === bank.company_id);
    const seen = new Set<string>();
    while (cur?.parent_company_id && !seen.has(cur.id)) {
      seen.add(cur.id);
      cur = dir.companies.find((c) => c.id === cur!.parent_company_id) || cur;
    }
    return cur?.id || bank.company_id;
  }, [dir, bank.company_id]);
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [period, setPeriod] = useState("");
  useEffect(() => { const t = setTimeout(() => setTerm(q), 300); return () => clearTimeout(t); }, [q]);
  const docType = bank.direction === "out" ? "payable" : "receivable,advance_receipt";
  const list = useApi<{ items: Doc[]; total: number }>("/documents", {
    doc_type: docType, company_id: root, settle_status: "open,partial", q: term || undefined, period: period || undefined,
    page_size: 100 });
  const [picked, setPicked] = useState<Map<string, Doc>>(new Map());
  // 系統建議的單據先勾好
  useEffect(() => {
    if (!list.data || picked.size || !preselect.length) return;
    const m = new Map<string, Doc>();
    list.data.items.filter((d) => preselect.includes(d.id)).forEach((d) => m.set(d.id, d));
    if (m.size) setPicked(m);
  }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = [...picked.values()].reduce((a, d) => a + (d.outstanding || 0), 0);
  const diff = bank.amount - total;
  const out = bank.direction === "out";
  const [mode, setMode] = useState<"leave_open" | "book">("book");
  const defCat = diff > 0 ? (out ? "E12" : "N03") : (out ? "N03" : "E11");
  const [cat, setCat] = useState("");
  useEffect(() => setCat(defCat), [defCat]);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  const toggle = (d: Doc) => setPicked((m) => { const n = new Map(m); n.has(d.id) ? n.delete(d.id) : n.set(d.id, d); return n; });
  const effMode = diff > 0 ? "book" : mode;
  const cats = categories.filter((c) => c.nature === "expense" || c.nature === "income");

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await apiSend("POST", `/review-items/${reviewId}/resolve`, { action: "manual_match", payload: {
        document_ids: [...picked.keys()], difference_mode: diff ? effMode : undefined,
        difference_category: diff && effMode === "book" ? cat : undefined, remember_account: remember } });
      onDone(`已核對 ${picked.size} 張${diff ? `，差額 ${money(Math.abs(diff))} ${effMode === "book" ? "已記帳" : "留著未" + (out ? "付" : "收")}` : ""}`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  return (
    <div className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>手動核對</strong>
        <span className="small">從 {companyName(root)}（含分公司）未{out ? "付" : "收"}的單據裡勾選</span>
        <label className="ctl"><span className="k">單據月份</span>
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} /></label>
        <label style={{ flex: "1 1 220px" }}><span className="sr">搜尋廠商、發票號碼</span>
          <input placeholder="搜尋廠商、摘要或發票號碼" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      <div className="tbl-wrap" style={{ maxHeight: 280, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
        <table>
          <thead><tr><th style={{ width: 32 }} /><th>對象</th><th>公司／門市</th><th>月份</th><th>發票</th><th className="r">未結清</th></tr></thead>
          <tbody>
            {list.loading && !list.data && <tr><td colSpan={6} className="small">讀取中…</td></tr>}
            {list.data?.items.length === 0 && <tr><td colSpan={6} className="small">沒有符合的未{out ? "付" : "收"}單據</td></tr>}
            {list.data?.items.map((d) => (
              <tr key={d.id} className="clickable" onClick={() => toggle(d)}>
                <td><input type="checkbox" checked={picked.has(d.id)} onChange={() => toggle(d)} onClick={(e) => e.stopPropagation()}
                           aria-label={`勾選 ${d.counterparty?.name || "單據"}`} style={{ width: 18, minHeight: 18 }} /></td>
                <td>{d.counterparty?.name || "（未指定）"}</td>
                <td className="small">{companyName(d.company_id)}・{d.store_ids.map((x) => storeName(x)).join("、") || "公司層級"}</td>
                <td className="num small">{d.period_keys.join("、")}</td>
                <td className="num small">{d.invoice_nos.join(" ") || "—"}</td>
                <td className="num r">{money(d.outstanding)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="kv">
        <div><div className="k">銀行金額</div><div className="num">{money(bank.amount)}</div></div>
        <div><div className="k">勾選 {picked.size} 張合計</div><div className="num">{money(total)}</div></div>
        <div><div className="k">差額</div><div className={`num ${diff ? "neg" : ""}`}>{diff === 0 ? "0（剛好）" : `${diff > 0 ? "多" : "少"}${out ? "付" : "收"} ${money(Math.abs(diff))}`}</div></div>
      </div>
      {picked.size > 0 && diff !== 0 && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          {diff < 0 && (
            <label className="ctl"><span className="k">差額怎麼處理</span>
              <select value={mode} onChange={(e) => setMode(e.target.value as "leave_open" | "book")}>
                <option value="book">單據結清，差額記一筆（{out ? "折讓、抹零" : "手續費被先扣"}）</option>
                <option value="leave_open">差額留著未{out ? "付" : "收"}（之後再{out ? "付" : "收"}）</option>
              </select></label>
          )}
          {effMode === "book" && (
            <label className="ctl"><span className="k">差額記到</span>
              <select value={cat} onChange={(e) => setCat(e.target.value)}>
                {cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}
              </select></label>
          )}
        </div>
      )}
      {out && bank.counterparty_account_no && (
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} style={{ width: 18, minHeight: 18 }} />
          記住對方帳號 {bank.counterparty_account_no} 是這家廠商（原本記在別家的會改過來）
        </label>
      )}
      <FieldErrors error={err} />
      <div><button className="btn pri" disabled={busy || !picked.size} onClick={submit}>
        {busy ? "處理中…" : `核對這 ${picked.size} 張`}</button></div>
    </div>
  );
}
