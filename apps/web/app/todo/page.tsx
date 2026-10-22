"use client";
/** S3 會計待辦：各公司本月還缺什麼。資料：/periods/{company}/{period}、/review-items。 */
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { PeriodPicker, useQuery } from "@/components/filters";
import { useDirectory } from "@/components/directory";
import { Empty, ErrorBox, Mark, Skeleton, Tag } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { ChecklistItem, Company } from "@/lib/types";

type PeriodState = { status: string; checklist: ChecklistItem[] };

export default function TodoPage() {
  const q = useQuery();
  const { dir, loading } = useDirectory();
  const reviews = useApi<{ items: unknown[] }>("/review-items");
  return (
    <>
      <PageHeader crumb="帳務" title="會計待辦" sub={`${q.period}・各公司月結進度`}>
        <PeriodPicker />
        <Link className="btn" href={q.keep("/imports")}>上傳檔案</Link>
        <Link className="btn" href={q.keep("/review")}>處理待確認{reviews.data ? ` ${reviews.data.items.length}` : ""}</Link>
        <Link className="btn pri" href={q.keep("/documents/new")}>新增支出</Link>
      </PageHeader>
      {loading && <Skeleton rows={6} />}
      {!loading && dir.companies.length === 0 && <Empty title="沒有可管理的公司" />}
      <section aria-label="各公司月結進度" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
        {dir.companies.map((c) => <CompanyCard key={c.id} c={c} period={q.period} closeHref={q.link("/close", { company: c.id })} />)}
      </section>
    </>
  );
}

function CompanyCard({ c, period, closeHref }: { c: Company; period: string; closeHref: string }) {
  const st = useApi<PeriodState>(`/periods/${c.id}/${period}`);
  const failed = st.data?.checklist.filter((i) => i.status === "failed") || [];
  const closed = st.data?.status === "closed";
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column" }}>
      <div className="card-h">
        <h2>{c.name}</h2>
        {st.data && (closed ? <Tag kind="ok">已關帳</Tag> : failed.length ? <Tag kind="warn">{failed.length} 項未過</Tag> : <Tag kind="ok">可以關帳</Tag>)}
      </div>
      {st.loading && <Skeleton rows={5} height={12} />}
      {st.error && <div className="pad"><ErrorBox error={st.error} onRetry={st.reload} /></div>}
      {st.data && st.data.checklist.map((i) => (
        <div key={i.code} className="chk" style={{ padding: "7px 16px", fontSize: 13 }}>
          <Mark status={i.status} />
          <span style={{ flex: 1 }}>{i.label}</span>
          <span className="small" style={{ textAlign: "right" }}>{i.status === "na" ? "不適用" : i.note}</span>
        </div>
      ))}
      <div style={{ padding: 12, marginTop: "auto" }}>
        <Link className="btn" style={{ width: "100%" }} href={closeHref}>{closed ? "查看月結" : failed.length ? "前往月結" : "關帳"}</Link>
      </div>
    </div>
  );
}
