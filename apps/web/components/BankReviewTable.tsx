"use client";
/**
 * 待確認裡的銀行交易（銀行有、單據無）：列表一起看、批次處理。
 * - 依現金流分類分群（薪資、未歸類…），可依公司、方向、月份、關鍵字篩選
 * - 勾選後批次：建立已結清單據（類別＋損益月份：同月／上個月／指定，例：9 月匯的薪資記 8 月）、確認系統建議、略過
 * - 每列可指定門市（例：員工屬於哪家店），批次列可一次套用；選過的對象下次自動帶出
 * - 點一列展開：單筆處理與手動核對（金額有差、帳號不對）
 * 資料：GET /review-items?kind=bank_only；批次：POST /review-items/batch。
 */
import { Fragment, ReactNode, useMemo, useState } from "react";
import { useDirectory } from "./directory";
import { useToast } from "./ui";
import { apiSend } from "@/lib/api";
import { money } from "@/lib/format";
import type { ReviewItem } from "@/lib/types";

/** 現金流分類 → 建議的損益類別與月份（薪資、勞健保多半是付上個月的） */
const SUGGEST: Record<string, { cat: string; mode: "same" | "prev" }> = {
  EXP_SAL: { cat: "P01", mode: "prev" },
  EXP_SOC: { cat: "P03", mode: "prev" },
  EXP_UTI: { cat: "O02", mode: "same" },
  EXP_LEA: { cat: "O01", mode: "same" },
  REV_INT: { cat: "N03", mode: "same" },
};

const monthOf = (it: ReviewItem) => String(it.payload.item?.txn_date || "").slice(0, 7);
const catOf = (it: ReviewItem) => it.payload.item?.cash_category_name || "（現金流未分類）";

export function BankReviewTable({ items, onDone, renderDetail }: {
  items: ReviewItem[]; onDone: (ids: string[]) => void; renderDetail: (it: ReviewItem) => ReactNode;
}) {
  const toast = useToast();
  const { dir, companyName, categories } = useDirectory();
  const [company, setCompany] = useState("");
  const [direction, setDirection] = useState("");
  const [cat, setCat] = useState("");
  const [month, setMonth] = useState("");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  // 門市：預設帶系統記住的（對方戶名上次選的門市），空字串＝公司層級
  const [storeOf, setStoreOf] = useState<Record<string, string>>({});
  // 預設：先前選過的門市；公司（含分公司）只有一家門市時直接帶那家（例：楊師傅＝青年店）
  const storeFor = (it: ReviewItem) => {
    if (storeOf[it.id] !== undefined) return storeOf[it.id];
    if (it.suggested_store_id) return it.suggested_store_id;
    const only = dir.stores.filter((st) => rootOf(st.company_id) === rootOf(it.company_id));
    return only.length === 1 ? only[0].id : "";
  };
  const storesFor = (cid: string) => dir.stores.filter((s) => rootOf(s.company_id) === rootOf(cid));
  const [applyStore, setApplyStore] = useState("");

  const months = useMemo(() => [...new Set(items.map(monthOf))].sort(), [items]);
  const rootOf = (cid: string) => {
    let c = dir.companies.find((x) => x.id === cid);
    while (c?.parent_company_id) c = dir.companies.find((x) => x.id === c!.parent_company_id) || undefined;
    return c?.id || cid;
  };
  const base = items.filter((it) => {
    const b = it.payload.item || {};
    if (company && rootOf(it.company_id) !== company) return false;
    if (direction && b.direction !== direction) return false;
    if (month && monthOf(it) !== month) return false;
    if (q) {
      const hay = `${b.bank_memo || ""} ${b.bank_note || ""} ${b.counterparty_name || ""} ${b.amount}`;
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const groups = useMemo(() => {
    const m = new Map<string, { n: number; out: number; inn: number }>();
    base.forEach((it) => {
      const g = m.get(catOf(it)) || { n: 0, out: 0, inn: 0 };
      g.n += 1;
      if (it.payload.item?.direction === "out") g.out += it.payload.item.amount; else g.inn += it.payload.item?.amount || 0;
      m.set(catOf(it), g);
    });
    return [...m.entries()].sort((a, b) => b[1].n - a[1].n);
  }, [base]);
  const activeCat = groups.some(([g]) => g === cat) ? cat : "";   // 那一群處理完了就回到全部
  const shown = base.filter((it) => !activeCat || catOf(it) === activeCat);
  const allPicked = shown.length > 0 && shown.every((it) => picked.has(it.id));
  const sel = items.filter((it) => picked.has(it.id));
  const selOut = sel.filter((it) => it.payload.item?.direction === "out").reduce((a, it) => a + it.payload.item.amount, 0);
  const selIn = sel.filter((it) => it.payload.item?.direction === "in").reduce((a, it) => a + it.payload.item.amount, 0);
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setPicked((s) => {
    const n = new Set(s);
    shown.forEach((it) => (allPicked ? n.delete(it.id) : n.add(it.id)));
    return n;
  });

  // 批次設定：依勾選的現金流分類帶建議
  const codes = [...new Set(sel.map((it) => it.payload.item?.cash_category_code).filter(Boolean))];
  const sug = codes.length === 1 ? SUGGEST[codes[0] as string] : undefined;
  const [bCat, setBCat] = useState("");
  const [bMode, setBMode] = useState<"same" | "prev" | "fixed">("same");
  const [bMonth, setBMonth] = useState("");
  const effCat = bCat || sug?.cat || "";
  const effMode = bCat || bMode !== "same" ? bMode : sug?.mode || "same";
  const mixedDir = selOut > 0 && selIn > 0;
  const natures = selIn > 0 && !selOut ? ["income", "balance"] : ["expense", "balance"];
  const withSuggestion = sel.filter((it) => it.payload.suggestion?.documents?.length);
  const selRoots = new Set(sel.map((it) => rootOf(it.company_id)));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ label: string; ok: number; failed: { id: string; message: string }[];
    period?: string; company?: string } | null>(null);

  async function batch(action: string, payload: Record<string, unknown>, ids: string[], label: string) {
    setBusy(true);
    try {
      const r = await apiSend<{ ok: string[]; failed: { id: string; message: string }[] }>("POST", "/review-items/batch",
        { ids, action, payload });
      onDone(r.ok);
      setResult({ label, ok: r.ok.length, failed: r.failed,
                  period: action === "create_document" ? String(payload.period_key || periodLabel) : undefined,
                  company: sel[0]?.company_id });
      setPicked((s) => { const n = new Set(s); r.ok.forEach((id) => n.delete(id)); return n; });
      toast(r.failed.length ? `${label} ${r.ok.length} 筆，${r.failed.length} 筆失敗：${r.failed[0].message}` : `${label} ${r.ok.length} 筆`,
            r.failed.length ? "error" : "ok");
    } catch (e) {
      setResult({ label, ok: 0, failed: ids.map((id) => ({ id, message: (e as Error).message || "批次處理失敗" })) });
      toast((e as Error).message || "批次處理失敗", "error");
    } finally { setBusy(false); }
  }

  const periodLabel = effMode === "prev" ? "上個月" : effMode === "fixed" ? bMonth || "（選月份）" : "交易當月";

  return (
    <section className="card" aria-label="銀行交易待核對" style={{ marginBottom: 16 }}>
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <strong style={{ marginRight: 8 }}>銀行交易待核對 <span className="num sub">{items.length}</span></strong>
          <label className="ctl"><span className="k">公司</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              <option value="">全部</option>
              {dir.companies.filter((c) => !c.parent_company_id).map((c) => <option key={c.id} value={c.id}>{c.name}（含分公司）</option>)}
            </select></label>
          <label className="ctl"><span className="k">方向</span>
            <select value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="">全部</option><option value="out">支出</option><option value="in">收入</option>
            </select></label>
          <label className="ctl"><span className="k">交易月份</span>
            <select value={month} onChange={(e) => setMonth(e.target.value)}>
              <option value="">全部</option>{months.map((m) => <option key={m} value={m}>{m}</option>)}
            </select></label>
          <label style={{ flex: "1 1 200px" }}><span className="sr">搜尋</span>
            <input placeholder="搜尋摘要、備註、對方戶名、金額" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        </div>
        <div role="group" aria-label="現金流分類" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="chip" aria-pressed={!activeCat} onClick={() => setCat("")}>全部 <span className="num">{base.length}</span></button>
          {groups.map(([g, v]) => (
            <button key={g} className="chip" aria-pressed={activeCat === g} onClick={() => setCat(activeCat === g ? "" : g)}>
              {g} <span className="num">{v.n}</span></button>
          ))}
        </div>
      </div>

      {result && (
        <div role="status" style={{ margin: "12px 14px 0", padding: "10px 12px", borderRadius: 8,
                                   border: `1px solid ${result.failed.length ? "var(--neg, #b91c1c)" : "var(--line)"}`,
                                   background: result.failed.length ? "#fef2f2" : "var(--surface-2)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong>{result.label} {result.ok} 筆{result.failed.length ? `，${result.failed.length} 筆失敗` : ""}</strong>
            {result.ok > 0 && result.period && result.company &&
              <a className="btn sm" href={`/pl?period=${/^\d{4}-\d{2}$/.test(result.period) ? result.period : ""}&company=${rootOf(result.company)}`}>
                看損益表{/^\d{4}-\d{2}$/.test(result.period) ? `（${result.period}）` : ""}</a>}
            <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => setResult(null)}>關閉</button>
          </div>
          {result.failed.length > 0 && (
            <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {[...new Set(result.failed.map((f) => f.message))].slice(0, 5).map((m) => (
                <li key={m}>{m}（{result.failed.filter((f) => f.message === m).length} 筆）</li>))}
            </ul>
          )}
        </div>
      )}

      {sel.length > 0 && (
        <div className="ai" style={{ margin: "12px 14px", display: "grid", gap: 10 }}>
          <div style={{ fontWeight: 600 }}>已勾選 {sel.length} 筆
            {selOut > 0 && <>・支出 <span className="num">{money(selOut)}</span></>}
            {selIn > 0 && <>・收入 <span className="num">{money(selIn)}</span></>}
            <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => setPicked(new Set())}>取消勾選</button></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label className="ctl"><span className="k">損益類別</span>
              <select value={effCat} onChange={(e) => setBCat(e.target.value)} disabled={mixedDir}>
                <option value="">選擇類別</option>
                {categories.filter((c) => natures.includes(c.nature)).map((c) =>
                  <option key={c.code} value={c.code}>{c.code} {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</option>)}
              </select></label>
            <label className="ctl"><span className="k">損益月份</span>
              <select value={effMode} onChange={(e) => { setBMode(e.target.value as "same" | "prev" | "fixed"); if (!bCat && effCat) setBCat(effCat); }}>
                <option value="same">交易當月</option><option value="prev">上個月（例：9 月匯 8 月薪資）</option><option value="fixed">指定月份</option>
              </select></label>
            {effMode === "fixed" && <label className="ctl"><span className="k">月份</span>
              <input type="month" value={bMonth} onChange={(e) => setBMonth(e.target.value)} /></label>}
            <button className="btn pri" disabled={busy || !effCat || mixedDir || (effMode === "fixed" && !bMonth)}
                    onClick={() => batch("create_document", {
                      category_code: effCat, period_mode: effMode, period_key: bMonth || undefined,
                      store_by_item: Object.fromEntries(sel.map((it) => [it.id, storeFor(it)])) }, [...picked], "已建立已結清單據")}>
              建立 {sel.length} 筆已結清單據（記在{periodLabel}）</button>
            {withSuggestion.length > 0 &&
              <button className="btn" disabled={busy} onClick={() => batch("settle_suggestion", {}, withSuggestion.map((it) => it.id), "已依建議結清")}>
                確認系統建議 {withSuggestion.length} 筆</button>}
            <button className="btn" disabled={busy} onClick={() => batch("dismiss", {}, [...picked], "已略過")}>略過 {sel.length} 筆</button>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label className="ctl"><span className="k">門市</span>
              <select value={applyStore} onChange={(e) => setApplyStore(e.target.value)} disabled={selRoots.size !== 1}>
                <option value="">公司層級（不分門市）</option>
                {selRoots.size === 1 && storesFor(sel[0].company_id).map((s) => <option key={s.id} value={s.id}>{s.code} {s.name}</option>)}
              </select></label>
            <button className="btn" disabled={selRoots.size !== 1}
                    onClick={() => setStoreOf((m) => ({ ...m, ...Object.fromEntries(sel.map((it) => [it.id, applyStore])) }))}>
              套用到勾選的 {sel.length} 筆</button>
            {selRoots.size > 1 && <span className="small">勾選的跨不同公司，門市請逐列選</span>}
          </div>
          {mixedDir && <div className="small neg">支出與收入要分開建立單據（類別不同），請用上方「方向」篩選。</div>}
          {sug && !bCat && <div className="small">依現金流分類建議：{sug.cat}、{sug.mode === "prev" ? "上個月" : "交易當月"}，可以改。</div>}
          <div className="small">對象沿用每筆的對方戶名；門市照每列「門市」欄（沒選＝公司層級），選過的人下次自動帶出。</div>
        </div>
      )}

      <div className="tbl-wrap" style={open ? { overflow: "auto" } : { maxHeight: 560, overflow: "auto" }}>
        <table>
          <thead style={{ position: "sticky", top: 0, zIndex: 1 }}><tr>
            <th style={{ width: 36 }}><input type="checkbox" aria-label="全選目前清單" checked={allPicked} disabled={!shown.length}
                                              onChange={toggleAll} style={{ width: 18, minHeight: 18 }} /></th>
            <th>交易日</th><th>公司・帳戶</th><th>摘要／備註／對方</th><th className="r">支出</th><th className="r">收入</th>
            <th>現金流分類</th><th>門市</th><th>系統比對結果</th><th style={{ width: 28 }} />
          </tr></thead>
          <tbody>
            {shown.map((it) => {
              const b = it.payload.item || {};
              const isOpen = open === it.id;
              return (
                <Fragment key={it.id}>
                  <tr className={`clickable ${picked.has(it.id) ? "sel" : ""}`} onClick={() => setOpen(isOpen ? null : it.id)}>
                    <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={picked.has(it.id)} onChange={() => toggle(it.id)}
                                                                     aria-label="勾選" style={{ width: 18, minHeight: 18 }} /></td>
                    <td className="num">{b.txn_date}</td>
                    <td className="small">{companyName(it.company_id)}<div className="sub">{b.cash_account_name || b.cash_account_id}</div></td>
                    <td className="small">{b.bank_memo}<div className="sub">{[b.bank_note, b.counterparty_name].filter((x, i, a) => x && a.indexOf(x) === i).join("・")}</div></td>
                    <td className="num r">{b.direction === "out" ? money(b.amount) : ""}</td>
                    <td className="num r">{b.direction === "in" ? money(b.amount) : ""}</td>
                    <td className="small">{catOf(it)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <select aria-label="門市" value={storeFor(it)} style={{ minHeight: 32, fontSize: 13, maxWidth: 140 }}
                              onChange={(e) => setStoreOf((m) => ({ ...m, [it.id]: e.target.value }))}>
                        <option value="">公司層級</option>
                        {storesFor(it.company_id).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select></td>
                    <td className="small">{it.payload.reason}{it.payload.suggestion?.documents?.length ? <div className="sub">有建議單據</div> : null}</td>
                    <td aria-hidden>{isOpen ? "▾" : "▸"}</td>
                  </tr>
                  {isOpen && <tr><td colSpan={10} style={{ background: "var(--surface-2)" }}>{renderDetail(it)}</td></tr>}
                </Fragment>
              );
            })}
            {!shown.length && <tr><td colSpan={10} className="small" style={{ padding: 16 }}>沒有符合的交易</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
