"use client";
/** 單據側欄：明細、結清紀錄、來源。損益表下鑽、應付帳款、待確認共用。 */
import { useState } from "react";
import { apiSend, useApi } from "@/lib/api";
import { money, settleLabel, SOURCE_LABEL } from "@/lib/format";
import type { Doc } from "@/lib/types";
import { useDirectory } from "./directory";
import { ErrorBox, Skeleton, Tag, useToast } from "./ui";

const CASH_SOURCE: Record<string, string> = { "cash:petty_cash": "零用金", "cash:store_cash": "門市收銀", "cash:advance": "老闆／員工代墊" };

export type RelinkTarget = { doc: Doc; bank: { cash_txn_id: string; company_id: string; direction: "out" | "in"; amount: number } };

export function DocumentDrawer({ id, onClose, onChanged, onRelink }: {
  id: string; onClose: () => void; onChanged?: () => void;
  /** 有給才顯示「改對應單據」：由頁面開一個寬的手動核對區 */
  onRelink?: (t: RelinkTarget) => void;
}) {
  const { data: d, error, loading, reload } = useApi<Doc>(`/documents/${id}`);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function undoCash(paymentId: string) {
    setBusy(true);
    try {
      const r = await apiSend<{ reopened_documents: string[] }>("POST", `/cash-payments/${paymentId}/reverse`, {});
      toast(r.reopened_documents.length > 1 ? `已取消這筆現金付款（${r.reopened_documents.length} 張單據一起回到未付）` : "已取消這筆現金付款");
      reload(); onChanged?.();
    } catch (e) { toast((e as Error).message || "取消失敗", "error"); } finally { setBusy(false); }
  }
  async function unmatch() {
    setBusy(true);
    try {
      const r = await apiSend<{ reopened: number; failed: { message: string }[] }>("POST", "/documents/batch-unmatch", { ids: [id] });
      if (r.failed.length) toast(r.failed[0].message, "error");
      else toast("已取消核對，銀行交易送回「待確認」");
      reload(); onChanged?.();
    } catch (e) { toast((e as Error).message || "取消失敗", "error"); } finally { setBusy(false); }
  }
  function relinkTarget(): RelinkTarget | null {
    if (!d || d.status === "void" || (d.bank_txn_ids || []).length !== 1) return null;
    const txn = d.bank_txn_ids![0];
    const sts = (d.settlements || []).filter((s) => s.cash_txn_id === txn && !s.reversed);
    const amount = sts.find((s) => s.bank_amount)?.bank_amount || sts.reduce((a, s) => a + s.amount, 0) || d.settle_amount || 0;
    return { doc: d, bank: { cash_txn_id: txn, company_id: d.company_id, direction: d.doc_type === "payable" ? "out" : "in", amount } };
  }
  const { storeName, categoryName, companyName } = useDirectory();
  return (
    <aside className="card aside drawer" aria-label="單據明細">
      <div className="card-h">
        <div>
          <div className="crumb">{d ? `${companyName(d.company_id)}・${d.doc_date}` : "單據"}</div>
          <h2 style={{ fontSize: 16 }}>{d?.counterparty?.name || "單據明細"}</h2>
        </div>
        <button className="btn sm" onClick={onClose} aria-label="關閉明細">✕</button>
      </div>
      {loading && !d && <Skeleton rows={6} />}
      {error && <div className="pad"><ErrorBox error={error} onRetry={reload} /></div>}
      {d && (
        <div className="stack" style={{ padding: "0 16px 16px" }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Tag kind="src">{SOURCE_LABEL[d.source_kind] || d.source_kind}</Tag>
            {d.status === "draft" && <Tag kind="warn">草稿</Tag>}
            {d.status === "void" && <Tag kind="neg">已作廢</Tag>}
            {d.settle_status !== "not_applicable" && <Tag kind={settleLabel(d.settle_status, d.doc_type)[1].slice(2) as never}>
              {settleLabel(d.settle_status, d.doc_type)[0]}</Tag>}
            {d.counterparty?.is_intercompany && <Tag>集團內</Tag>}
          </div>
          {d.memo && <div className="sub">{d.memo}</div>}
          {d.status !== "void" && !!d.bank_txn_ids?.length && (
            <div className="ai" style={{ display: "grid", gap: 8 }}>
              <div className="small">{d.source_kind === "bank_only"
                ? "這張是由銀行交易直接建立的。如果同一筆錢已經有 POS 或發票的單據（例：刷卡入帳、現金存入），請改對應到那些單據，避免重複計算。"
                : "這張單據由銀行交易結清。對錯了可以取消核對，銀行交易會回到「待確認」重新對應。"}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {onRelink && relinkTarget() && <button className="btn pri sm" disabled={busy} onClick={() => onRelink(relinkTarget()!)}>
                  改對應單據{d.doc_type !== "payable" ? "（例：POS 應收）" : ""}</button>}
                <button className="btn sm" disabled={busy} onClick={unmatch}>取消核對，送回待確認</button>
              </div>
            </div>
          )}
          <div className="kv">
            {d.settle_amount !== null && <div><div className="k">應付／應收（含稅）</div><div className="num">{money(d.settle_amount, { zeroDash: false })}</div></div>}
            {d.settle_amount !== null && <div><div className="k">未結清</div><div className="num">{money(d.outstanding, { zeroDash: false })}</div></div>}
            <div><div className="k">稅額</div><div className="num">{money(d.tax_amount, { zeroDash: false })}</div></div>
            <div><div className="k">到期日</div><div className="num">{d.due_date || "—"}</div></div>
            <div><div className="k">發票</div><div className="num">{d.invoice_nos.length ? d.invoice_nos.join("、") : d.has_invoice ? "有" : "無發票"}</div></div>
          </div>
          <div>
            <div className="small" style={{ marginBottom: 4 }}>明細（未稅）</div>
            <table>
              <tbody>
                {d.lines?.map((l) => (
                  <tr key={l.line_no}>
                    <td><div>{l.category_code} {categoryName(l.category_code)}</div>
                      <div className="small">{l.period_key}・{storeName(l.store_id)}</div></td>
                    <td className="num r">{money(l.amount, { zeroDash: false })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!!d.settlements?.length && (
            <div>
              <div className="small" style={{ marginBottom: 4 }}>結清紀錄</div>
              {d.settlements.map((s, i) => (
                <div key={i} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "4px 0" }}>
                  <span>{s.txn_date}・{s.kind === "cash" ? `現金（${CASH_SOURCE[s.cash_account_id] || "現金"}）` : `銀行 ${s.cash_account_id}`}
                    {s.reversed ? "（已取消）" : ""}{s.note ? <div className="sub">{s.note}</div> : null}</span>
                  <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <span className="num">{money(s.amount)}</span>
                    {s.kind === "cash" && !s.reversed &&
                      <button className="btn sm" disabled={busy} onClick={() => undoCash(s.cash_txn_id)}>取消</button>}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
