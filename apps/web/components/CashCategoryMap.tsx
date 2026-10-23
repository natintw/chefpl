"use client";
/**
 * 現金流分類對照：現金流系統（ChefCashFlow）先把每筆銀行交易分好類，這裡決定每個分類預設記到損益表哪一行、哪個月。
 * 待確認的「建立已結清單據」會預填這裡的類別；同一家廠商在上面「廠商類別」設過的，以廠商類別為準。
 * 資料：GET /settings/cash-category-map、PATCH /settings/cash-category-map/{code}
 */
import { useState } from "react";
import { useDirectory } from "./directory";
import { ErrorBox, FieldErrors, Skeleton, Tag, useToast } from "./ui";
import { ApiError, apiSend, useApi } from "@/lib/api";

type Row = { cash_code: string; cash_name: string; ledger_category_code: string | null; ledger_category_name: string | null;
  ledger_nature: string | null; period_mode: "same" | "prev"; note: string | null; updated_by: string; open_reviews: number };

const NO_MATCH = new Set(["CSH_WDL", "CSH_DEP", "INT_TRN", "INT_REV", "ADJ_COR"]);

export function CashCategoryMap() {
  const { categories } = useDirectory();
  const toast = useToast();
  const list = useApi<{ items: Row[] }>("/settings/cash-category-map");
  const [over, setOver] = useState<Record<string, Partial<Row>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const rows = (list.data?.items || []).map((r) => ({ ...r, ...over[r.cash_code] }));
  const unmapped = rows.filter((r) => !r.ledger_category_code && !NO_MATCH.has(r.cash_code) && r.open_reviews > 0);

  async function save(r: Row, patch: { ledger_category_code?: string | null; period_mode?: string }) {
    setBusy(r.cash_code); setErr(null);
    try {
      await apiSend("PATCH", `/settings/cash-category-map/${encodeURIComponent(r.cash_code)}`,
        { ledger_category_code: "ledger_category_code" in patch ? patch.ledger_category_code : r.ledger_category_code,
          period_mode: patch.period_mode || r.period_mode });
      setOver((o) => ({ ...o, [r.cash_code]: { ...o[r.cash_code], ...patch } as Partial<Row> }));
      toast(`${r.cash_name} 已更新，待確認的預設類別會跟著改`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  }

  return (
    <section className="card" aria-label="現金流分類對照" style={{ marginTop: 20 }}>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
        <div style={{ fontWeight: 600 }}>現金流分類對照</div>
        <div className="small">現金流系統匯入對帳單時已先分好類（進貨付款、薪資、水電…）。這張表決定每個分類在待確認時預設記到損益表哪一行、哪個月；
          同一家廠商在上面設過類別的，以廠商類別為準。現金流系統新增的分類會自動出現在這裡。</div>
        {unmapped.length > 0 && <div className="small warnv" style={{ marginTop: 6 }}>
          {unmapped.map((r) => r.cash_name).join("、")} 還沒設定，待確認裡有 {unmapped.reduce((a, r) => a + r.open_reviews, 0)} 筆沒有預設類別。</div>}
      </div>
      <FieldErrors error={err} />
      {list.loading && !list.data && <Skeleton rows={6} />}
      {list.error && <div className="pad"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
      {rows.length > 0 && (
        <div className="tbl-wrap">
          <table>
            <thead><tr><th>現金流分類</th><th style={{ minWidth: 220 }}>預設損益類別</th><th>損益月份</th>
              <th className="r">待確認</th><th>說明</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const skip = NO_MATCH.has(r.cash_code);
                return (
                  <tr key={r.cash_code}>
                    <td><div style={{ fontWeight: 500 }}>{r.cash_name}</div><div className="small num">{r.cash_code}</div></td>
                    <td>{skip ? <Tag>不需核對、不入損益</Tag> : (
                      <select aria-label={`${r.cash_name} 的損益類別`} value={r.ledger_category_code || ""} disabled={busy === r.cash_code}
                              onChange={(e) => save(r, { ledger_category_code: e.target.value || null })}>
                        <option value="">不預填（人工選）</option>
                        {categories.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</option>)}
                      </select>)}
                    </td>
                    <td>{skip ? "—" : (
                      <select aria-label={`${r.cash_name} 的損益月份`} value={r.period_mode} disabled={busy === r.cash_code}
                              onChange={(e) => save(r, { period_mode: e.target.value })}>
                        <option value="same">交易當月</option>
                        <option value="prev">上個月</option>
                      </select>)}
                    </td>
                    <td className="num r">{r.open_reviews || "—"}</td>
                    <td className="small">{r.note || ""}{r.updated_by !== "seed" && r.updated_by !== "cashflow" ? `（${r.updated_by} 改過）` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
