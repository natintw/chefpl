"use client";
/**
 * 銀行建立的單據 ↔ POS／發票單據 配對預覽與套用。
 * 來源：勾選的單據（POST /documents/link-plan）或自動配對（POST /documents/auto-link-plan）；套用：POST /documents/link-apply。
 * 每組可取消勾選；金額有差的組，差額處理方式統一選（少收預設「留著未收」，多收一定要記一個類別）。
 */
import { useMemo, useState } from "react";
import { useDirectory } from "./directory";
import { FieldErrors, Tag, useToast } from "./ui";
import { ApiError, apiSend } from "@/lib/api";
import { money } from "@/lib/format";

type Card = { id: string; name: string; date: string; amount: number; source_kind: string; store_ids: string[] };
export type LinkGroup = { bank_document: Card & { txn: string }; documents: Card[]; total: number; difference: number;
  how: string; hint_dates?: string[] };
export type LinkPlan = { groups: LinkGroup[]; unmatched_bank?: Card[]; unmatched_documents?: Card[]; skipped?: number };

const HOW: Record<string, string> = { same_amount: "金額相同", sum: "加總相同", rest: "其餘合併", note_date: "依銀行備註日期",
  same_amount_near: "7 天內同金額" };

export function BankLinkPlan({ plan, title, receivable, onDone, onClose }: {
  plan: LinkPlan; title: string; receivable: boolean; onDone: () => void; onClose: () => void;
}) {
  const toast = useToast();
  const { storeName, categories } = useDirectory();
  const [on, setOn] = useState<Set<string>>(new Set(plan.groups.map((g) => g.bank_document.id)));
  const [mode, setMode] = useState<"leave_open" | "book">("leave_open");
  const [cat, setCat] = useState(receivable ? "E11" : "N03");
  const [overCat, setOverCat] = useState(receivable ? "N03" : "E12");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const chosen = plan.groups.filter((g) => on.has(g.bank_document.id));
  const short = chosen.some((g) => g.difference < 0), over = chosen.some((g) => g.difference > 0);
  const cats = useMemo(() => categories.filter((c) => c.nature === "expense" || c.nature === "income"), [categories]);
  const verb = receivable ? "收" : "付";

  async function apply() {
    setBusy(true); setErr(null);
    try {
      const r = await apiSend<{ linked: number; failed: { message: string }[] }>("POST", "/documents/link-apply", {
        groups: chosen.map((g) => ({
          bank_document_id: g.bank_document.id, document_ids: g.documents.map((d) => d.id),
          ...(g.difference > 0 ? { difference_mode: "book", difference_category: overCat }
            : g.difference < 0 ? { difference_mode: mode, difference_category: mode === "book" ? cat : undefined } : {}),
        })) });
      toast(`已連結 ${r.linked} 組${r.failed.length ? `；${r.failed.length} 組失敗：${r.failed[0].message}` : ""}`,
            r.failed.length ? "error" : undefined);
      onDone();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  return (
    <div style={{ margin: "12px 14px", padding: "12px 14px", border: "1px solid var(--accent)", borderRadius: 10, display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <div><strong>{title}</strong>
          <div className="small">左邊是銀行那筆（來源：銀行），右邊是要連過去的 POS／發票單據。確認後銀行建立的那張作廢，右邊變成已{verb}，
            {receivable ? "入款" : "付款"}日＝銀行日期，營收（費用）只算一次。</div></div>
        <button className="btn sm" onClick={onClose}>關閉</button>
      </div>
      {plan.groups.length === 0 && <div className="small">沒有找到有把握的配對{plan.skipped ? `（${plan.skipped} 筆銀行單據找不到對應，請個別「改對應」）` : ""}。</div>}
      {plan.groups.length > 0 && (
        <div className="tbl-wrap" style={{ maxHeight: 420, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
          <table>
            <thead><tr><th style={{ width: 32 }}>
              <input type="checkbox" aria-label="全選配對" style={{ width: 18, minHeight: 18 }}
                     checked={chosen.length === plan.groups.length}
                     onChange={() => setOn(chosen.length === plan.groups.length ? new Set() : new Set(plan.groups.map((g) => g.bank_document.id)))} /></th>
              <th>銀行（{receivable ? "入款" : "付款"}日）</th><th className="r">銀行金額</th><th>對到</th><th className="r">單據合計</th><th className="r">差額</th><th>依據</th></tr></thead>
            <tbody>
              {plan.groups.map((g) => (
                <tr key={g.bank_document.id} className="clickable" onClick={() => setOn((s) => {
                  const n = new Set(s); n.has(g.bank_document.id) ? n.delete(g.bank_document.id) : n.add(g.bank_document.id); return n; })}>
                  <td><input type="checkbox" checked={on.has(g.bank_document.id)} readOnly aria-label={`配對 ${g.bank_document.name}`}
                             style={{ width: 18, minHeight: 18 }} /></td>
                  <td><div className="num">{g.bank_document.date}</div><div className="small">{g.bank_document.name}</div></td>
                  <td className="num r">{money(g.bank_document.amount)}</td>
                  <td className="small">{g.documents.map((d) => (
                    <div key={d.id}><span className="num">{d.date}</span> {d.name}{d.store_ids.length ? `・${d.store_ids.map(storeName).join("、")}` : ""}
                      <span className="num">　{money(d.amount)}</span></div>))}</td>
                  <td className="num r">{money(g.total)}</td>
                  <td className={`num r ${g.difference ? "neg" : ""}`}>{g.difference ? money(g.difference) : "0"}</td>
                  <td><Tag kind={g.difference ? "warn" : "ok"}>{HOW[g.how] || g.how}</Tag></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(plan.unmatched_bank?.length || plan.unmatched_documents?.length) ? (
        <div className="small warnv">沒配到：{[...(plan.unmatched_bank || []).map((c) => `銀行 ${c.date} ${money(c.amount)}`),
          ...(plan.unmatched_documents || []).map((c) => `${c.name} ${c.date} ${money(c.amount)}`)].join("、")}</div>) : null}
      {(short || over) && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          {short && <label className="ctl"><span className="k">銀行比單據少</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as "leave_open" | "book")}>
              <option value="leave_open">差額留著未{verb}</option><option value="book">單據結清，差額記一筆</option></select></label>}
          {short && mode === "book" && <label className="ctl"><span className="k">少{verb}記到</span>
            <select value={cat} onChange={(e) => setCat(e.target.value)}>{cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}</select></label>}
          {over && <label className="ctl"><span className="k">銀行比單據多，多的記到</span>
            <select value={overCat} onChange={(e) => setOverCat(e.target.value)}>{cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}</select></label>}
        </div>
      )}
      <FieldErrors error={err} />
      {plan.groups.length > 0 && <div><button className="btn pri" disabled={busy || !chosen.length} onClick={apply}>
        {busy ? "處理中…" : `連結這 ${chosen.length} 組`}</button></div>}
    </div>
  );
}
