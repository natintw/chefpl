"use client";
/** S7 匯入：應付表、薪資表。先上傳預覽（不入帳），沒有錯誤才能確認入帳。資料：POST /imports、POST /imports/{id}/commit。 */
import Link from "next/link";
import { useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, FieldErrors, Mark, Tag, useToast } from "@/components/ui";
import { ApiError, apiSend, apiUpload } from "@/lib/api";
import { money } from "@/lib/format";

type Batch = { id: string; kind: string; company_id: string; period: string; file_name: string; status: string;
  row_count: number; total_amount: number; warnings: { row: number; code?: string; message: string }[];
  errors: { row: number; code?: string; message: string }[];
  preview: { row: number; counterparty: string | null; settle_amount: number; status: string | null;
    lines: { category_code: string | null; amount: number; store_id?: string | null; description?: string | null }[] }[] };
type Committed = { batch_id: string; created: number; drafts_for_review: number; total_amount: number; superseded_batches: string[] };

const KINDS = {
  ap_sheet: { label: "應付帳款表", hint: "每月的進貨與費用總表（Excel）。同月份重新上傳會取代上一次的批次。" },
  payroll: { label: "薪資表", hint: "每門市一列：薪資、勞健保、勞退。同公司同月同門市重新匯入會取代舊的。" },
} as const;
type K = keyof typeof KINDS;

export default function ImportsPage() {
  const q = useQuery();
  const { dir, companyName, storeName, categoryName } = useDirectory();
  const toast = useToast();
  const [kind, setKind] = useState<K>((q.get("kind") as K) || "ap_sheet");
  const [company, setCompany] = useState(q.company || "");
  const [period, setPeriod] = useState(q.period);
  const [store, setStore] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [done, setDone] = useState<Committed | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const companyId = company || dir.companies[0]?.id || "";
  const stores = dir.stores.filter((s) => s.company_id === companyId);

  async function upload() {
    if (!file) return;
    setBusy(true); setErr(null); setBatch(null); setDone(null);
    const f = new FormData();
    f.set("kind", kind); f.set("company_id", companyId); f.set("period", period);
    if (store) f.set("store_id", store);
    f.set("file", file);
    try { setBatch(await apiUpload<Batch>("/imports", f)); }
    catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }
  async function commit() {
    if (!batch) return;
    setBusy(true); setErr(null);
    try {
      const r = await apiSend<Committed>("POST", `/imports/${batch.id}/commit`);
      setDone(r); toast(`已入帳 ${r.created} 張單據`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }
  function reset() {
    setBatch(null); setDone(null); setFile(null); setErr(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  const drafts = batch?.preview.filter((p) => p.status === "draft").length || 0;

  return (
    <>
      <PageHeader crumb="作業" title="匯入" sub="上傳後先預覽，不會馬上入帳；確認沒問題再按入帳。" />
      <div className="row">
        <section className="card pad stack" aria-label="上傳" style={{ flex: "0 1 380px", alignSelf: "flex-start" }}>
          <div className="seg" role="group" aria-label="檔案類型">
            {(Object.keys(KINDS) as K[]).map((k) => <button key={k} aria-pressed={kind === k} onClick={() => { setKind(k); reset(); }}>{KINDS[k].label}</button>)}
          </div>
          <p className="small" style={{ margin: 0 }}>{KINDS[kind].hint}</p>
          <label className="f"><span>公司</span>
            <select value={companyId} onChange={(e) => { setCompany(e.target.value); setStore(""); }}>
              {dir.companies.map((c) => <option key={c.id} value={c.id}>{c.parent_company_id ? "　└ " : ""}{c.name}</option>)}
            </select></label>
          <label className="f"><span>月份</span><input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} /></label>
          {kind === "ap_sheet" && (
            <label className="f"><span>門市（選填）</span>
              <select value={store} onChange={(e) => setStore(e.target.value)}>
                <option value="">依表內欄位／公司層級</option>
                {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select></label>
          )}
          <label className="f"><span>檔案（.xlsx／.csv）</span>
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" onChange={(e) => { setFile(e.target.files?.[0] || null); setBatch(null); setDone(null); }} /></label>
          <FieldErrors error={!batch ? err : null} />
          <button className="btn pri" disabled={busy || !file || !companyId} onClick={upload}>{busy && !batch ? "解析中…" : "上傳並預覽"}</button>
          <p className="small" style={{ margin: 0 }}>【待確認】實際應付表與薪資表格式尚未提供，目前解析器依示意欄位；拿到真實檔案後會調整。</p>
        </section>

        <section className="card grow" aria-label="預覽" style={{ minWidth: 0 }}>
          {!batch && <Empty title="還沒有預覽">選好檔案按「上傳並預覽」，這裡會列出每一列解析的結果。</Empty>}
          {batch && (
            <>
              <div className="card-h">
                <div><h2>{batch.file_name}</h2>
                  <div className="small">{KINDS[batch.kind as K]?.label}・{companyName(batch.company_id)}・{batch.period}</div></div>
                {done ? <Tag kind="ok">已入帳</Tag> : batch.errors.length ? <Tag kind="neg">{batch.errors.length} 個錯誤</Tag> : <Tag kind="est">預覽中</Tag>}
              </div>
              <div className="kv pad">
                <div><div className="k">列數</div><div className="num">{batch.row_count}</div></div>
                <div><div className="k">合計（含稅）</div><div className="num">{money(batch.total_amount)}</div></div>
                <div><div className="k">要人工分類</div><div className="num">{drafts}</div></div>
                <div><div className="k">提醒</div><div className="num">{batch.warnings.length}</div></div>
              </div>

              {(batch.errors.length > 0 || batch.warnings.length > 0) && (
                <div className="pad stack" style={{ paddingTop: 0 }}>
                  {batch.errors.map((e, i) => <div key={`e${i}`} className="chk"><Mark status="error" /><span>{e.row ? `第 ${e.row} 列：` : ""}{e.message}</span></div>)}
                  {batch.warnings.slice(0, 30).map((w, i) => <div key={`w${i}`} className="chk"><Mark status="warn" /><span className="small">{w.row ? `第 ${w.row} 列：` : ""}{w.message}</span></div>)}
                  {batch.warnings.length > 30 && <div className="small">另有 {batch.warnings.length - 30} 則提醒</div>}
                </div>
              )}

              {batch.preview.length > 0 && (
                <div className="tbl-wrap" style={{ maxHeight: 460 }}>
                  <table>
                    <thead><tr><th className="r">列</th><th>對象</th><th>類別</th><th>門市</th><th className="r">金額</th><th>狀態</th></tr></thead>
                    <tbody>
                      {batch.preview.map((p) => {
                        const cats = [...new Set(p.lines.map((l) => l.category_code).filter(Boolean))] as string[];
                        const st = [...new Set(p.lines.map((l) => l.store_id || null))];
                        return (
                          <tr key={p.row}>
                            <td className="num r small">{p.row}</td>
                            <td>{p.counterparty || "—"}</td>
                            <td>{cats.length ? cats.map((c) => `${c} ${categoryName(c)}`).join("、") : <span className="warnv">未分類</span>}</td>
                            <td className="small">{st.map((s) => storeName(s)).join("、")}</td>
                            <td className="num r">{money(p.settle_amount)}</td>
                            <td>{p.status === "draft" ? <Tag kind="warn">待分類</Tag> : <Tag kind="ok">可入帳</Tag>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="pad stack" style={{ borderTop: "1px solid var(--line)" }}>
                <FieldErrors error={err} />
                {done ? (
                  <>
                    <p style={{ margin: 0 }}>已建立 <b className="num">{done.created}</b> 張單據，合計 <span className="num">{money(done.total_amount)}</span>。
                      {done.drafts_for_review ? <> 其中 {done.drafts_for_review} 張要人工分類，已放進待確認。</> : null}
                      {done.superseded_batches.length ? <> 已取代同月份舊批次 {done.superseded_batches.length} 個。</> : null}</p>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {done.drafts_for_review > 0 && <Link className="btn pri" href={q.keep("/review")}>處理待確認</Link>}
                      <Link className="btn" href={q.link("/payables", { period: batch.period, company: batch.company_id })}>看應付帳款</Link>
                      <button className="btn" onClick={reset}>再匯入一份</button>
                    </div>
                  </>
                ) : (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button className="btn pri" disabled={busy || batch.errors.length > 0 || batch.status !== "parsed"} onClick={commit}>
                      {batch.errors.length ? "有錯誤，請修正檔案後重新上傳" : busy ? "入帳中…" : `確認入帳 ${batch.row_count} 列`}</button>
                    <button className="btn" disabled={busy} onClick={reset}>取消</button>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
