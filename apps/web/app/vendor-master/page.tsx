"use client";
/**
 * S13 廠商主檔（開發文件 v0.6 5.17）：廠商名稱與統編集中維護。預付沖抵、折讓對單、廠商搜尋都讀這一份。
 * 資料：GET/POST/PATCH /vendors、POST /vendors/import（commit=false 預覽、true 寫入）、GET /vendors/import-template
 */
import { useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, FieldErrors, Skeleton, Tag, useToast } from "@/components/ui";
import { ApiError, apiDownload, apiSend, apiUpload, useApi } from "@/lib/api";

type Vendor = { id: string; name: string; tax_id: string | null; id_masked: string | null; no_tax_id: boolean;
  missing_tax_id: boolean; aliases: string[]; keywords: string[]; default_category_code: string | null;
  payment_rule: string; is_intercompany: boolean; is_active: boolean };
type Row = { row: number; action: "create" | "fill_tax_id" | "exists" | "name_differs" | "error" | "skip"; name: string;
  tax_id: string | null; message: string; counterparty_id: string | null; current_name: string | null; inactive: boolean };
type Report = { committed: boolean; counts: Record<string, number>; rows: Row[] };

const ACTION: Record<Row["action"], [string, "ok" | "src" | "est" | "warn" | "neg"]> = {
  create: ["新增", "ok"], fill_tax_id: ["補統編", "src"], exists: ["已存在", "est"], name_differs: ["名稱不同", "warn"],
  error: ["錯誤", "neg"], skip: ["略過", "est"],
};
type Filter = "all" | "missing" | "ic" | "inactive";

export default function VendorMasterPage() {
  const toast = useToast();
  const { categories } = useDirectory();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const list = useApi<{ items: Vendor[]; missing_tax_id_count: number }>("/vendors", { include_inactive: true });
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", tax_id: "", no_tax_id: false, alias: "", category: "", payment_rule: "next_month_30" });
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [decisions, setDecisions] = useState<Record<number, string>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const cats = categories.filter((c) => c.is_active && (c.nature === "expense" || c.nature === "balance"));

  const items = list.data?.items || [];
  const shown = items.filter((v) => (filter === "all" ? v.is_active : filter === "missing" ? v.missing_tax_id && v.is_active
      : filter === "ic" ? v.is_intercompany : !v.is_active)
    && (!q || v.name.includes(q) || (v.tax_id || "").includes(q) || v.aliases.some((a) => a.includes(q))));
  const count = (f: Filter) => items.filter((v) => (f === "all" ? v.is_active : f === "missing" ? v.missing_tax_id && v.is_active
    : f === "ic" ? v.is_intercompany : !v.is_active)).length;

  async function create() {
    setBusy("create"); setErr(null);
    try {
      const v = await apiSend<Vendor>("POST", "/vendors", {
        name: form.name, tax_id: form.no_tax_id ? null : form.tax_id || null, no_tax_id: form.no_tax_id,
        aliases: form.alias ? form.alias.split(/[、,，]/).map((s) => s.trim()).filter(Boolean) : [],
        keywords: form.alias ? [form.alias.split(/[、,，]/)[0].trim()] : [],
        default_category_code: form.category || null, payment_rule: form.payment_rule });
      toast(`已新增廠商：${v.name}`);
      setForm({ name: "", tax_id: "", no_tax_id: false, alias: "", category: "", payment_rule: "next_month_30" });
      list.reload();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  }

  async function saveTax(v: Vendor) {
    setBusy(v.id); setErr(null);
    try {
      const t = (edit[v.id] || "").trim();
      await apiSend("PATCH", `/vendors/${v.id}`, t === "-" ? { no_tax_id: true } : { tax_id: t });
      toast(t === "-" ? `${v.name}：已標記為沒有統編` : `${v.name}：已補統編 ${t}`);
      setEdit((e) => { const n = { ...e }; delete n[v.id]; return n; });
      list.reload();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  }

  async function runImport(commit: boolean) {
    if (!file) return;
    setBusy(commit ? "commit" : "preview"); setErr(null);
    const fd = new FormData();
    fd.set("file", file); fd.set("commit", String(commit)); fd.set("decisions", JSON.stringify(decisions));
    try {
      const r = await apiUpload<Report>("/vendors/import", fd);
      setReport(r);
      if (commit) {
        toast(`已匯入：新增 ${r.counts.create || 0}、補統編 ${r.counts.fill_tax_id || 0}、名稱不同 ${r.counts.name_differs || 0}`);
        setFile(null); setDecisions({}); if (fileRef.current) fileRef.current.value = ""; list.reload();
      }
    } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  }

  return (
    <>
      <PageHeader crumb="設定" title="廠商主檔" sub="所有畫面的廠商搜尋、預付沖抵、折讓對單，都讀這一份名單。新增廠商一定要有統編，個人或小販勾「沒有統編」。">
        <button className="btn" disabled={busy === "tpl"} onClick={async () => {
          setBusy("tpl"); try { await apiDownload("/vendors/import-template", "廠商主檔匯入範本.xlsx"); } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
        }}>下載匯入範本</button>
      </PageHeader>
      <FieldErrors error={err} />

      {(list.data?.missing_tax_id_count || 0) > 0 && (
        <div className="errbox" role="status" style={{ marginBottom: 16 }}>
          <span style={{ flex: "1 1 240px" }}>有 {list.data!.missing_tax_id_count} 家廠商還沒有統編：這些廠商的預付沒辦法自動比對，折讓也只能用名稱找原單。</span>
          <button className="btn sm" onClick={() => setFilter("missing")}>只看缺統編的</button>
        </div>
      )}

      <div className="row">
        <section className="card grow" aria-label="廠商清單" style={{ minWidth: 0 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
            <label style={{ flex: "1 1 240px" }}><span className="sr">搜尋統編或名稱</span>
              <input placeholder="搜尋統編或名稱" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            <div className="seg" role="group" aria-label="篩選" style={{ flexWrap: "wrap" }}>
              {([["all", "全部"], ["missing", "缺統編"], ["ic", "集團內"], ["inactive", "已停用"]] as [Filter, string][]).map(([k, t]) => (
                <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{t} <span className="num">{count(k)}</span></button>
              ))}
            </div>
          </div>
          {list.loading && !list.data && <Skeleton rows={8} />}
          {list.error && <div className="pad"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
          {list.data && shown.length === 0 && <Empty title={items.length ? "沒有符合的廠商" : "還沒有任何廠商"}>可以用右側表單新增，或用 Excel 批次匯入。</Empty>}
          {shown.length > 0 && (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>廠商名稱</th><th>統編</th><th>簡稱</th><th>預設類別</th><th><span className="sr">動作</span></th></tr></thead>
                <tbody>
                  {shown.slice(0, 300).map((v) => (
                    <tr key={v.id}>
                      <td style={{ whiteSpace: "normal" }}><span style={{ fontWeight: 500 }}>{v.name}</span>{v.is_intercompany && <> <Tag>集團內</Tag></>}{!v.is_active && <> <Tag kind="est">停用</Tag></>}</td>
                      <td className="num">{v.tax_id || (v.id_masked ? <>{v.id_masked} <Tag kind="est">個人</Tag></> : v.no_tax_id ? <Tag kind="est">無統編</Tag>
                        : edit[v.id] !== undefined ? (
                          <span style={{ display: "inline-flex", gap: 6 }}>
                            <input className="num" style={{ width: 110 }} aria-label={`${v.name} 的統編`} value={edit[v.id]} placeholder="8 碼；沒有填 -"
                                   onChange={(e) => setEdit((x) => ({ ...x, [v.id]: e.target.value.trim() }))} />
                            <button className="btn sm pri" disabled={busy === v.id} onClick={() => saveTax(v)}>儲存</button>
                          </span>) : <Tag kind="warn">缺統編</Tag>)}</td>
                      <td className="small" style={{ whiteSpace: "normal" }}>{v.aliases.join("、") || "—"}</td>
                      <td className="small">{v.default_category_code || "—"}</td>
                      <td className="r">{v.missing_tax_id && edit[v.id] === undefined &&
                        <button className="linkbtn small" onClick={() => setEdit((x) => ({ ...x, [v.id]: "" }))}>補統編</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {shown.length > 300 && <div className="small pad">只顯示前 300 家，請用搜尋縮小範圍。</div>}
            </div>
          )}
        </section>

        <form className="card aside pad stack" aria-labelledby="new-v" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <h2 id="new-v">新增廠商</h2>
          <label className="f"><span>廠商名稱（發票上的全名）</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="f"><span>廠商統編</span>
            <input className="num" inputMode="numeric" disabled={form.no_tax_id} value={form.tax_id}
                   onChange={(e) => setForm({ ...form, tax_id: e.target.value.replace(/\D/g, "").slice(0, 8) })} />
            <span className="small">系統會檢查統編檢查碼；統編已存在時會帶出那一家</span></label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 32 }}>
            <input type="checkbox" checked={form.no_tax_id} onChange={(e) => setForm({ ...form, no_tax_id: e.target.checked })} />沒有統編（個人、小販）</label>
          <label className="f"><span>簡稱・付款關鍵字</span><input value={form.alias} onChange={(e) => setForm({ ...form, alias: e.target.value })} placeholder="例：富統" />
            <span className="small">搜尋用，也用來在銀行備註中辨識這家廠商</span></label>
          <label className="f"><span>預設類別</span>
            <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              <option value="">未設定</option>{cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}</select></label>
          <label className="f"><span>付款條件</span>
            <select value={form.payment_rule} onChange={(e) => setForm({ ...form, payment_rule: e.target.value })}>
              <option value="next_month_30">次月 30 日</option><option value="next_month_10">次月 10 日</option>
              <option value="same_month_end">當月底</option><option value="none">貨到付款</option></select></label>
          <button type="submit" className="btn pri" disabled={busy === "create" || !form.name || (!form.no_tax_id && form.tax_id.length !== 8)}>儲存廠商</button>
        </form>
      </div>

      <section className="card pad stack" aria-labelledby="imp" style={{ marginTop: 16 }}>
        <h2 id="imp">Excel 批次匯入</h2>
        <div className="small">欄位：<b>廠商名稱｜廠商統編</b>（必要），另可有 沒有統編（Y）、遮蔽身分證、簡稱、預設類別、付款關鍵字、狀態（停用）。
          有「匯入名單」工作表的檔案會優先讀它。先預覽、確認後才寫入；匯入只新增或補資料，不會刪除任何廠商。</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input ref={fileRef} type="file" accept=".xlsx" aria-label="選擇 Excel 檔"
                 onChange={(e) => { setFile(e.target.files?.[0] || null); setReport(null); setDecisions({}); }} />
          <button className="btn" disabled={!file || busy === "preview"} onClick={() => runImport(false)}>{busy === "preview" ? "預覽中…" : "預覽"}</button>
        </div>
        {report && (
          <>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(Object.keys(ACTION) as Row["action"][]).filter((a) => report.counts[a]).map((a) => (
                <Tag key={a} kind={ACTION[a][1]}>{ACTION[a][0]} {report.counts[a]}</Tag>))}
              {report.committed && <Tag kind="ok">已寫入</Tag>}
            </div>
            <div className="tbl-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
              <table>
                <thead><tr><th>列</th><th>檔案中的名稱</th><th>統編</th><th>結果</th><th>說明・要你決定的事</th></tr></thead>
                <tbody>
                  {report.rows.filter((r) => r.action !== "exists").map((r) => (
                    <tr key={r.row}>
                      <td className="num">{r.row}</td><td style={{ whiteSpace: "normal" }}>{r.name}</td><td className="num">{r.tax_id || "—"}</td>
                      <td><Tag kind={ACTION[r.action][1]}>{ACTION[r.action][0]}</Tag>{r.inactive && <> <Tag kind="est">停用</Tag></>}</td>
                      <td style={{ whiteSpace: "normal" }}>{r.action === "name_differs" && !report.committed ? (
                        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", gap: "4px 14px", flexWrap: "wrap" }}>
                          <legend className="small" style={{ padding: 0 }}>主檔是「{r.current_name}」，要用哪個名稱？</legend>
                          {[["keep_name", "保留主檔名稱"], ["update_name", "改成檔案中的名稱"], ["skip", "這列不處理"]].map(([k, t]) => (
                            <label key={k} style={{ display: "flex", gap: 6, alignItems: "center", minHeight: 32 }}>
                              <input type="radio" name={`r${r.row}`} checked={(decisions[r.row] || "keep_name") === k}
                                     onChange={() => setDecisions((d) => ({ ...d, [r.row]: k }))} />{t}</label>))}
                        </fieldset>) : <span className={r.action === "error" ? "neg" : ""}>{r.message}{r.current_name && r.action === "fill_tax_id" ? `（主檔：${r.current_name}）` : ""}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!report.committed && (
              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap", alignItems: "center" }}>
                {report.counts.error ? <span className="small">錯誤的 {report.counts.error} 列不會匯入，其他列可以先匯入</span> : null}
                <button className="btn pri" disabled={busy === "commit"} onClick={() => runImport(true)}>確認匯入（略過錯誤列）</button>
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}
