"use client";
/** S9 月結：逐公司看檢核清單、關帳、重開。資料：GET /periods/{c}/{p}、POST .../close、.../reopen。 */
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, FieldErrors, Mark, Modal, Skeleton, Tag, useToast } from "@/components/ui";
import { ApiError, apiSend, useApi } from "@/lib/api";
import type { ChecklistItem } from "@/lib/types";

type PeriodState = { company_id: string; period: string; status: string; checklist: ChecklistItem[] };

/** 每個檢核項目沒過時，該去哪裡處理 */
const FIX_LINK: Record<string, [string, string]> = {
  revenue: ["/documents/new", "補登營收"],
  payroll: ["/imports", "匯入薪資"],
  recurring: ["/documents/new", "補登固定費用"],
  invoices: ["/payables", "看應付"],
  reviews: ["/review", "處理待確認"],
  accruals: ["/documents/new", "補預估入帳"],
  advances: ["/payables?type=advance_receipt", "轉營收"],
  b99: ["/review", "改歸 B99"],
};

export default function ClosePage() {
  const q = useQuery();
  const { dir, me, loading } = useDirectory();
  const companies = dir.companies;
  const company = q.company || companies[0]?.id || "";
  const [ack, setAck] = useState(false);
  const st = useApi<PeriodState>(company ? `/periods/${company}/${q.period}` : null, { acknowledge_unallocated: ack || undefined });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [reopen, setReopen] = useState(false);
  const [reason, setReason] = useState("");
  const toast = useToast();

  const failed = st.data?.checklist.filter((i) => i.status === "failed") || [];
  const closed = st.data?.status === "closed";
  const hasUnalloc = st.data?.checklist.some((i) => i.code === "allocation" && (i.status === "failed" || ack));
  const canClose = me?.role === "admin" || me?.role === "accountant";
  const canReopen = me?.role === "admin";

  async function doClose() {
    setBusy(true); setErr(null);
    try {
      await apiSend("POST", `/periods/${company}/${q.period}/close`, { acknowledge_unallocated: ack });
      toast(`${q.period} 已關帳`); st.reload();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }
  async function doReopen() {
    setBusy(true); setErr(null);
    try {
      await apiSend("POST", `/periods/${company}/${q.period}/reopen`, { reason });
      toast(`${q.period} 已重開`); setReopen(false); setReason(""); st.reload();
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader crumb="作業" title="月結" sub="清單全部通過才能關帳；關帳後這個月份不能再新增或修改單據。">
        <PeriodPicker />
      </PageHeader>
      {loading && <Skeleton rows={3} />}
      {!loading && companies.length === 0 && <Empty title="沒有可管理的公司" />}

      {companies.length > 0 && (
        <div className="seg" role="tablist" aria-label="公司" style={{ marginBottom: 16, flexWrap: "wrap" }}>
          {companies.map((c) => (
            <button key={c.id} role="tab" aria-selected={company === c.id} aria-pressed={company === c.id}
                    onClick={() => { setAck(false); setErr(null); q.set({ company: c.id, store: null }); }}>{c.name}</button>
          ))}
        </div>
      )}

      {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
      {st.loading && !st.data && <div className="card"><Skeleton rows={7} /></div>}
      {st.data && (
        <div className="row">
          <section className="card grow" aria-label="檢核清單" style={{ maxWidth: 760 }}>
            <div className="card-h">
              <h2>{q.period} 檢核清單</h2>
              {closed ? <Tag kind="ok">已關帳</Tag> : failed.length ? <Tag kind="warn">{failed.length} 項未過</Tag> : <Tag kind="ok">可以關帳</Tag>}
            </div>
            {st.data.checklist.map((i) => {
              const fix = FIX_LINK[i.code];
              return (
                <div key={i.code} className="chk" style={{ padding: "10px 16px", alignItems: "center" }}>
                  <Mark status={i.status} />
                  <div style={{ flex: 1 }}>
                    <div>{i.label}</div>
                    {i.note && <div className="small">{i.status === "na" ? "不適用" : i.note}</div>}
                  </div>
                  {i.status === "failed" && fix && !closed && <Link className="btn sm" href={q.keep(fix[0])}>{fix[1]}</Link>}
                </div>
              );
            })}

            {hasUnalloc && !closed && (
              <label style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "12px 16px", borderTop: "1px solid var(--line)", fontSize: 13 }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} style={{ width: 18, minHeight: 18, marginTop: 2 }} />
                <span>我知道有費用留在公司層級、還沒分到門市，這個月先這樣關帳。<span className="small">（門市損益會少算這部分）</span></span>
              </label>
            )}

            <div className="pad stack" style={{ borderTop: "1px solid var(--line)" }}>
              <FieldErrors error={err} />
              {!closed && canClose && (
                <button className="btn pri" disabled={busy || failed.length > 0} onClick={doClose}>
                  {failed.length ? `還有 ${failed.length} 項沒過，不能關帳` : `關帳 ${q.period}`}
                </button>
              )}
              {closed && canReopen && <button className="btn" disabled={busy} onClick={() => setReopen(true)}>重開這個月</button>}
              {closed && !canReopen && <p className="small" style={{ margin: 0 }}>已關帳。需要修改請找管理者重開。</p>}
              {!canClose && !closed && <p className="small" style={{ margin: 0 }}>只有會計與管理者可以關帳。</p>}
            </div>
          </section>
        </div>
      )}

      {reopen && (
        <Modal title={`重開 ${q.period}`} onClose={() => setReopen(false)}>
          <p className="sub" style={{ margin: 0 }}>重開後可以修改這個月的單據，已經給出去的報表數字可能會變。原因會記在稽核紀錄。</p>
          <label className="f"><span>原因</span>
            <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例：漏登 9 月電費" /></label>
          <FieldErrors error={err} />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setReopen(false)}>取消</button>
            <button className="btn pri" disabled={busy || reason.trim().length < 2} onClick={doReopen}>確認重開</button>
          </div>
        </Modal>
      )}
    </>
  );
}
