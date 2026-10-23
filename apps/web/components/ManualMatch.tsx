"use client";
/**
 * 待確認的銀行交易：手動挑單據核對。
 * 未付（未收）單據依「廠商 → 損益月份 → 單據 → 明細」展開，跟應付帳款頁同一份資料：
 * 每個月有小計與「從最舊累計」，剛好等於銀行金額的會標出來；可整月勾選，也可逐張勾。
 * 金額可以有些許差距（匯費、抹零、手續費被先扣、分次付），差額選「留著未付」或「記一張單據」；
 * 支出時會記住對方帳號（原本記在別家廠商的會改過來），下次自動對上。
 * 資料：GET /documents?with_lines=true（同一法人、未結清）；送出：POST /review-items/{id}/resolve action=manual_match。
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { useDirectory } from "./directory";
import { FieldErrors } from "./ui";
import { ApiError, apiSend, useApi } from "@/lib/api";
import { money } from "@/lib/format";
import type { Doc } from "@/lib/types";

type Bank = { cash_txn_id: string; company_id: string; direction: "out" | "in"; amount: number; counterparty_account_no?: string | null };
type Month = { key: string; docs: Doc[]; sum: number; cum: number };
type Vendor = { key: string; name: string; months: Month[]; sum: number; count: number };

const monthOf = (d: Doc) => d.period_keys.join("、") || d.doc_date.slice(0, 7);
const owe = (d: Doc) => d.outstanding || 0;

export function ManualMatch({ reviewId, bank, preselect, hint, onDone }: {
  reviewId: string; bank: Bank; preselect: string[]; hint?: string | null; onDone: (msg: string) => void;
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
  const out = bank.direction === "out";
  // 系統認得出廠商時先帶入，清掉就看全部廠商
  const [q, setQ] = useState(hint || "");
  const [term, setTerm] = useState(hint || "");
  const [period, setPeriod] = useState("");
  useEffect(() => { const t = setTimeout(() => setTerm(q), 300); return () => clearTimeout(t); }, [q]);
  const docType = out ? "payable" : "receivable,advance_receipt";
  const list = useApi<{ items: Doc[]; total: number }>("/documents", {
    doc_type: docType, company_id: root, settle_status: "open,partial", q: term || undefined, period: period || undefined,
    page_size: 500, with_lines: "true" });

  // 廠商 → 月份（舊到新）→ 單據（日期舊到新）
  const vendors = useMemo<Vendor[]>(() => {
    const by = new Map<string, Doc[]>();
    for (const d of list.data?.items || []) {
      const k = d.counterparty?.id || "-";
      by.set(k, [...(by.get(k) || []), d]);
    }
    const vs = [...by.entries()].map(([key, docs]) => {
      const ms = new Map<string, Doc[]>();
      docs.forEach((d) => ms.set(monthOf(d), [...(ms.get(monthOf(d)) || []), d]));
      let cum = 0;
      const months = [...ms.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([mk, ds]) => {
        const sum = ds.reduce((a, d) => a + owe(d), 0);
        cum += sum;
        return { key: mk, docs: ds.sort((a, b) => a.doc_date.localeCompare(b.doc_date)), sum, cum };
      });
      return { key, name: docs[0].counterparty?.name || "（未指定對象）", months, sum: cum, count: docs.length };
    });
    // 系統認出的廠商排最前，其餘依名稱
    return vs.sort((a, b) => Number(b.name === hint) - Number(a.name === hint) || a.name.localeCompare(b.name, "zh-Hant"));
  }, [list.data, hint]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const isOpen = (k: string) => open.has(k);
  const flip = (k: string) => setOpen((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  // 只有一家、或系統認出的那家，預設展開到月份
  useEffect(() => {
    if (!vendors.length) return;
    setOpen((s) => {
      const n = new Set(s);
      vendors.filter((v, i) => vendors.length === 1 || v.name === hint || (i === 0 && vendors.length <= 3))
        .forEach((v) => n.add(`v:${v.key}`));
      return n;
    });
  }, [vendors, hint]);

  const [picked, setPicked] = useState<Map<string, Doc>>(new Map());
  // 系統建議的單據先勾好
  useEffect(() => {
    if (!list.data || picked.size || !preselect.length) return;
    const m = new Map<string, Doc>();
    list.data.items.filter((d) => preselect.includes(d.id)).forEach((d) => m.set(d.id, d));
    if (m.size) setPicked(m);
  }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (d: Doc) => setPicked((m) => { const n = new Map(m); n.has(d.id) ? n.delete(d.id) : n.set(d.id, d); return n; });
  const setMany = (docs: Doc[], on: boolean) => setPicked((m) => {
    const n = new Map(m); docs.forEach((d) => (on ? n.set(d.id, d) : n.delete(d.id))); return n; });
  // 勾到這個月為止（從最舊開始全部勾），用來核「一次付好幾個月」
  const pickThrough = (v: Vendor, upto: number) => setPicked((m) => {
    const n = new Map(m);
    v.months.forEach((mo, i) => mo.docs.forEach((d) => (i <= upto ? n.set(d.id, d) : n.delete(d.id))));
    return n;
  });

  const total = [...picked.values()].reduce((a, d) => a + owe(d), 0);
  const diff = bank.amount - total;
  const [mode, setMode] = useState<"leave_open" | "book">("book");
  const defCat = diff > 0 ? (out ? "E12" : "N03") : (out ? "N03" : "E11");
  const [cat, setCat] = useState("");
  useEffect(() => setCat(defCat), [defCat]);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const effMode = diff > 0 ? "book" : mode;
  const cats = categories.filter((c) => c.nature === "expense" || c.nature === "income");
  const catName = (code: string) => categories.find((c) => c.code === code)?.name || code;

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await apiSend("POST", `/review-items/${reviewId}/resolve`, { action: "manual_match", payload: {
        document_ids: [...picked.keys()], difference_mode: diff ? effMode : undefined,
        difference_category: diff && effMode === "book" ? cat : undefined, remember_account: remember } });
      onDone(`已核對 ${picked.size} 張${diff ? `，差額 ${money(Math.abs(diff))} ${effMode === "book" ? "已記帳" : "留著未" + (out ? "付" : "收")}` : ""}`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  const hit = (n: number) => n === bank.amount;
  const caret = (k: string) => <span aria-hidden style={{ display: "inline-block", width: 14 }}>{isOpen(k) ? "▾" : "▸"}</span>;
  const box = (checked: boolean, partial: boolean, onChange: () => void, label: string) => (
    <input type="checkbox" checked={checked} aria-label={label} style={{ width: 18, minHeight: 18 }}
           ref={(el) => { if (el) el.indeterminate = partial; }}
           onChange={onChange} onClick={(e) => e.stopPropagation()} />
  );
  const pickedSummary = useMemo(() => {
    const g = new Map<string, { n: number; sum: number }>();
    picked.forEach((d) => {
      const k = `${d.counterparty?.name || "（未指定）"} ${monthOf(d)}`;
      const x = g.get(k) || { n: 0, sum: 0 }; g.set(k, { n: x.n + 1, sum: x.sum + owe(d) });
    });
    return [...g.entries()];
  }, [picked]);

  return (
    <div className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>手動核對</strong>
        <span className="small">{companyName(root)}（含分公司）未{out ? "付" : "收"}帳款，依廠商、月份展開；點 ▸ 看每張單據與明細</span>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ flex: "1 1 220px" }}><span className="sr">搜尋廠商、發票號碼</span>
          <input placeholder="搜尋廠商、摘要或發票號碼（清空看全部廠商）" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <label className="ctl"><span className="k">單據月份</span>
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} /></label>
        {(q || period) && <button className="btn" onClick={() => { setQ(""); setPeriod(""); }}>看全部</button>}
      </div>

      <div className="tbl-wrap" style={{ maxHeight: 420, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
        <table>
          <thead><tr>
            <th style={{ width: 32 }} /><th>廠商／月份／單據</th><th>門市</th><th>發票</th>
            <th className="r">金額</th><th className="r">已{out ? "付" : "收"}</th><th className="r">未{out ? "付" : "收"}</th>
            <th className="r" title="從最舊的月份累計到這個月">累計</th>
          </tr></thead>
          <tbody>
            {list.loading && !list.data && <tr><td colSpan={8} className="small">讀取中…</td></tr>}
            {list.data && vendors.length === 0 && <tr><td colSpan={8} className="small">
              沒有符合的未{out ? "付" : "收"}單據{q ? `（搜尋「${q}」）— 按「看全部」或改搜尋字` : ""}</td></tr>}
            {vendors.map((v) => {
              const vk = `v:${v.key}`;
              const all = v.months.flatMap((m) => m.docs);
              const n = all.filter((d) => picked.has(d.id)).length;
              return (
                <Fragment key={v.key}>
                  <tr className="clickable" onClick={() => flip(vk)} style={{ background: "var(--surface-2)" }}>
                    <td>{box(n === all.length, n > 0 && n < all.length, () => setMany(all, n !== all.length), `全選 ${v.name}`)}</td>
                    <td>{caret(vk)}<strong>{v.name}</strong> <span className="small">{v.months.length} 個月・{v.count} 張</span>
                      {v.name === hint && <span className="tag t-src" style={{ marginLeft: 6 }}>系統認出</span>}</td>
                    <td /><td />
                    <td /><td />
                    <td className="num r"><strong>{money(v.sum)}</strong></td>
                    <td className="num r">{hit(v.sum) && <span className="tag t-ok">＝銀行金額</span>}</td>
                  </tr>
                  {isOpen(vk) && v.months.map((mo, mi) => {
                    const mk = `m:${v.key}:${mo.key}`;
                    const k = mo.docs.filter((d) => picked.has(d.id)).length;
                    return (
                      <Fragment key={mk}>
                        <tr className="clickable" onClick={() => flip(mk)}>
                          <td>{box(k === mo.docs.length, k > 0 && k < mo.docs.length, () => setMany(mo.docs, k !== mo.docs.length),
                                   `勾選 ${v.name} ${mo.key} 全部`)}</td>
                          <td style={{ paddingLeft: 26 }}>{caret(mk)}<span className="num">{mo.key}</span>
                            <span className="small">　{mo.docs.length} 張</span>
                            {hit(mo.sum) && <span className="tag t-ok" style={{ marginLeft: 6 }}>這個月＝銀行金額</span>}</td>
                          <td className="small">{[...new Set(mo.docs.flatMap((d) => d.store_ids))].map(storeName).join("、") || "公司層級"}</td>
                          <td />
                          <td className="num r small">{money(mo.docs.reduce((a, d) => a + (d.settle_amount || 0), 0))}</td>
                          <td className="num r small">{money(mo.docs.reduce((a, d) => a + d.settled_amount, 0))}</td>
                          <td className="num r">{money(mo.sum)}</td>
                          <td className="num r">
                            {hit(mo.cum) && mi > 0
                              ? <button className="tag t-ok" style={{ border: 0, cursor: "pointer" }}
                                        onClick={(e) => { e.stopPropagation(); pickThrough(v, mi); }}
                                        title="勾選從最舊到這個月的全部單據">{money(mo.cum)}＝銀行金額，全勾</button>
                              : <span className="small">{money(mo.cum)}</span>}
                          </td>
                        </tr>
                        {isOpen(mk) && mo.docs.map((d) => {
                          const dk = `d:${d.id}`;
                          return (
                            <Fragment key={d.id}>
                              <tr className="clickable" onClick={() => toggle(d)}
                                  style={picked.has(d.id) ? { background: "var(--accent-soft)" } : undefined}>
                                <td>{box(picked.has(d.id), false, () => toggle(d), `勾選 ${v.name} ${d.doc_date}`)}</td>
                                <td style={{ paddingLeft: 52 }}>
                                  <button className="small" aria-expanded={isOpen(dk)} title="看明細"
                                          style={{ border: 0, background: "none", cursor: "pointer", padding: 0 }}
                                          onClick={(e) => { e.stopPropagation(); flip(dk); }}>{caret(dk)}</button>
                                  <span className="num small">{d.doc_date}</span>
                                  {d.settle_status === "partial" && <span className="tag t-warn" style={{ marginLeft: 6 }}>付了一部分</span>}
                                  {d.due_date && <span className="small">　到期 {d.due_date}</span>}
                                </td>
                                <td className="small">{d.store_ids.map(storeName).join("、") || "公司層級"}</td>
                                <td className="num small">{d.invoice_nos.join(" ") || "—"}</td>
                                <td className="num r small">{money(d.settle_amount || 0)}</td>
                                <td className="num r small">{d.settled_amount ? money(d.settled_amount) : "—"}</td>
                                <td className="num r">{money(owe(d))}</td>
                                <td className="num r">{hit(owe(d)) && <span className="tag t-ok">＝銀行金額</span>}</td>
                              </tr>
                              {isOpen(dk) && (
                                <tr><td /><td colSpan={7} style={{ paddingLeft: 72 }}>
                                  <div className="small" style={{ display: "grid", gap: 2 }}>
                                    {d.memo && <div>摘要：{d.memo}</div>}
                                    {(d.lines || []).map((l) => (
                                      <div key={l.line_no} style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                                        <span className="num">{l.period_key}</span>
                                        <span>{l.category_code} {catName(l.category_code)}</span>
                                        <span>{l.store_id ? storeName(l.store_id) : "公司層級"}</span>
                                        <span style={{ flex: 1 }}>{l.description || ""}</span>
                                        <span className="num">未稅 {money(l.amount)}</span>
                                      </div>
                                    ))}
                                    {d.tax_amount ? <div>稅額 <span className="num">{money(d.tax_amount)}</span></div> : null}
                                  </div>
                                </td></tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </Fragment>
                    );
                  })}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {list.data && list.data.total > list.data.items.length &&
        <div className="small">只列出前 {list.data.items.length} 張（共 {list.data.total} 張），請用搜尋或月份縮小範圍。</div>}

      {pickedSummary.length > 0 && (
        <div className="small" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          已勾：{pickedSummary.map(([k, x]) => <span key={k} className="tag t-src">{k}・{x.n} 張 {money(x.sum)}</span>)}
          <button className="btn" style={{ minHeight: 28, padding: "0 8px" }} onClick={() => setPicked(new Map())}>全部取消</button>
        </div>
      )}
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
