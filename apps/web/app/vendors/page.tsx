"use client";
/**
 * 廠商類別對應表：廠商（以統一編號為準）→ 損益表的哪一行。
 * 發票系統只給廠商和金額，靠這張表決定類別。設定後，這家廠商等待分類的單據會一起入帳。
 * 資料：GET/PATCH /settings/vendor-map、POST /settings/vendor-map/import、GET /settings/vendor-map/export
 */
import { useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { CashCategoryMap } from "@/components/CashCategoryMap";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, FieldErrors, Skeleton, Tag, useToast } from "@/components/ui";
import { ApiError, apiDownload, apiSend, apiUpload, useApi } from "@/lib/api";
import { money } from "@/lib/format";

type Vendor = { id: string; name: string; tax_id: string | null; id_masked: string | null; aliases: string[];
  category_code: string | null; category_name: string | null; is_intercompany: boolean; doc_count: number;
  amount: number; drafts: number };
type ImportRes = { updated: number; created: number; unchanged: number; skipped_blank: number; applied_drafts: number;
  errors: { row: number; message: string }[] };

export default function VendorsPage() {
  const { categories } = useDirectory();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [onlyUnmapped, setOnlyUnmapped] = useState(false);
  const list = useApi<{ items: Vendor[] }>("/settings/vendor-map");
  const [overrides, setOverrides] = useState<Record<string, Partial<Vendor>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [imp, setImp] = useState<ImportRes | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const vendorCats = useMemo(() => categories.filter((c) => c.nature === "expense" || c.nature === "balance"), [categories]);
  const items = (list.data?.items || []).map((v) => ({ ...v, ...overrides[v.id] }));
  const unmapped = items.filter((v) => !v.category_code);
  const shown = items.filter((v) => (!onlyUnmapped || !v.category_code) &&
    (!q || v.name.includes(q) || (v.tax_id || "").includes(q) || v.aliases.some((a) => a.includes(q))));

  async function setCategory(v: Vendor, code: string) {
    setBusy(v.id); setErr(null);
    try {
      const r = await apiSend<{ category_code: string | null; applied_drafts: number }>("PATCH", `/settings/vendor-map/${v.id}`,
        { category_code: code || null });
      const cat = vendorCats.find((c) => c.code === code);
      setOverrides((o) => ({ ...o, [v.id]: { category_code: r.category_code, category_name: cat?.name || null,
        drafts: Math.max(0, v.drafts - r.applied_drafts) } }));
      toast(r.applied_drafts ? `${v.name} → ${code}，${r.applied_drafts} 張等待分類的單據已入帳` : `${v.name} → ${code || "未設定"}`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  }

  async function upload(f: File) {
    setBusy("upload"); setErr(null); setImp(null);
    const form = new FormData(); form.set("file", f);
    try {
      const r = await apiUpload<ImportRes>("/settings/vendor-map/import", form);
      setImp(r); setOverrides({}); list.reload();
      toast(`對應表已更新：新增 ${r.created}、修改 ${r.updated}，${r.applied_drafts} 張單據入帳`);
    } catch (e) { setErr(e as ApiError); } finally {
      setBusy(null); if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <>
      <PageHeader crumb="作業" title="廠商類別" sub="發票系統只給廠商和金額；這張表決定每家廠商記到損益表的哪一行。以統一編號為準。">
        <button className="btn" disabled={busy === "download"} onClick={async () => {
          setBusy("download");
          try { await apiDownload("/settings/vendor-map/export", "廠商類別對應表.xlsx"); } catch (e) { setErr(e as ApiError); }
          finally { setBusy(null); }
        }}>下載 Excel</button>
        <label className="btn pri" style={{ cursor: "pointer" }}>
          {busy === "upload" ? "上傳中…" : "上傳 Excel"}
          <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
      </PageHeader>

      <FieldErrors error={err} />
      {imp && (
        <div className="card pad" style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600 }}>上傳結果</div>
          <div className="sub">新增 {imp.created} 家、修改 {imp.updated} 家、沒變 {imp.unchanged} 家、類別空白略過 {imp.skipped_blank} 家；
            {imp.applied_drafts} 張等待分類的單據已入帳。</div>
          {imp.errors.length > 0 && (
            <div className="stack" style={{ marginTop: 8, gap: 4 }}>
              {imp.errors.map((e, i) => <div key={i} className="small neg">第 {e.row} 列：{e.message}</div>)}
            </div>
          )}
        </div>
      )}

      <section className="card" aria-label="廠商類別對應">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
          <label style={{ flex: "1 1 260px" }}><span className="sr">搜尋廠商名稱或統編</span>
            <input placeholder="搜尋廠商名稱、統編或其他寫法" value={q} onChange={(e) => setQ(e.target.value)} /></label>
          <div className="seg" role="group" aria-label="篩選">
            <button aria-pressed={!onlyUnmapped} onClick={() => setOnlyUnmapped(false)}>全部 <span className="num">{items.length}</span></button>
            <button aria-pressed={onlyUnmapped} onClick={() => setOnlyUnmapped(true)}>未設定 <span className="num">{unmapped.length}</span></button>
          </div>
        </div>
        {list.loading && !list.data && <Skeleton rows={8} />}
        {list.error && <div className="pad"><ErrorBox error={list.error} onRetry={list.reload} /></div>}
        {list.data && items.length === 0 && (
          <Empty title="還沒有任何廠商">匯入發票彙總表後，廠商會自動出現在這裡；也可以直接上傳整理好的對應表 Excel。</Empty>
        )}
        {list.data && items.length > 0 && shown.length === 0 && <Empty title="沒有符合的廠商" />}
        {shown.length > 0 && (
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>廠商</th><th>統一編號</th><th style={{ minWidth: 220 }}>類別</th><th className="r">張數</th>
                <th className="r">累計含稅</th><th>狀態</th></tr></thead>
              <tbody>
                {shown.map((v) => (
                  <tr key={v.id}>
                    <td><div style={{ fontWeight: 500 }}>{v.name}{v.is_intercompany && <> <Tag>集團內</Tag></>}</div>
                      {v.aliases.length > 0 && <div className="small">也寫作：{v.aliases.join("、")}</div>}</td>
                    <td className="num small">{v.tax_id || v.id_masked || "—"}{v.id_masked && <div className="small">個人</div>}</td>
                    <td>
                      <select aria-label={`${v.name} 的類別`} value={v.category_code || ""} disabled={busy === v.id || v.is_intercompany}
                              onChange={(e) => setCategory(v, e.target.value)}>
                        <option value="">未設定</option>
                        {vendorCats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</option>)}
                      </select>
                    </td>
                    <td className="num r">{v.doc_count || "—"}</td>
                    <td className="num r">{money(v.amount)}</td>
                    <td>{v.is_intercompany ? <Tag kind="src">自動 C99</Tag>
                      : !v.category_code ? <Tag kind="warn">未設定{v.drafts ? `・${v.drafts} 張等待` : ""}</Tag>
                      : <Tag kind="ok">已設定</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="small" style={{ padding: "12px 16px" }}>
          <p style={{ margin: "0 0 6px" }}>
            整批設定：Excel 一個工作表、四欄 <b>統一編號｜廠商名稱｜類別代碼｜類別名稱</b>。「類別代碼」和「類別名稱」填一個就好，兩個都填要一致。
            先「下載 Excel」改好再「上傳 Excel」；統編相同、名稱不同時以 Excel 的名稱為準，舊名稱會留作「也寫作」。
            集團內公司之間的進貨固定記 C99，不在表裡。
          </p>
          <details>
            <summary style={{ cursor: "pointer" }}>類別代碼一覽</summary>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "2px 16px", marginTop: 8 }}>
              {vendorCats.map((c) => <span key={c.code}><span className="num">{c.code}</span> {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</span>)}
            </div>
          </details>
        </div>
      </section>

      <CashCategoryMap />
    </>
  );
}
