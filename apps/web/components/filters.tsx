"use client";
/** 月份與範圍寫在網址上（?period=&company=&store=），切換畫面時保留（開發文件 11.3）。 */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { defaultPeriod, GROUP_NAME } from "@/lib/format";
import { useDirectory } from "./directory";

export function useQuery() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const get = (k: string) => sp.get(k) || "";
  const set = useCallback((patch: Record<string, string | null>) => {
    const p = new URLSearchParams(sp.toString());
    Object.entries(patch).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  }, [sp, router, pathname]);
  /** 帶著目前的月份與範圍前往 href；patch 可覆寫（null 表示拿掉）。 */
  const link = (href: string, patch: Record<string, string | null> = {}) => {
    const [path, own = ""] = href.split("?");
    const p = new URLSearchParams(keepParams(sp));
    new URLSearchParams(own).forEach((v, k) => p.set(k, v));
    Object.entries(patch).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)));
    const s = p.toString();
    return s ? `${path}?${s}` : path;
  };
  return { period: sp.get("period") || defaultPeriod(), company: get("company"), store: get("store"), get, set,
           keep: (href: string) => link(href), link };
}

function keepParams(sp: URLSearchParams): string {
  const p = new URLSearchParams();
  ["period", "company", "store"].forEach((k) => { const v = sp.get(k); if (v) p.set(k, v); });
  return p.toString();
}

export function PeriodPicker() {
  const q = useQuery();
  return (
    <label className="ctl">
      <span className="k">月份</span>
      <input type="month" value={q.period} className="num" aria-label="月份"
             onChange={(e) => e.target.value && q.set({ period: e.target.value })} />
    </label>
  );
}

export function ScopePicker({ allowGroup = true, withStore = true }: { allowGroup?: boolean; withStore?: boolean }) {
  const q = useQuery();
  const { dir } = useDirectory();
  const stores = dir.stores.filter((s) => !q.company || s.company_id === q.company
    || dir.companies.find((c) => c.id === s.company_id)?.parent_company_id === q.company);
  return (
    <>
      <label className="ctl">
        <span className="k">公司</span>
        <select value={q.company} aria-label="公司" onChange={(e) => q.set({ company: e.target.value || null, store: null })}>
          {allowGroup && <option value="">{GROUP_NAME}（全集團）</option>}
          {dir.companies.filter((c) => !c.parent_company_id).flatMap((c) => [
            <option key={c.id} value={c.id}>{c.name}</option>,
            ...dir.companies.filter((b) => b.parent_company_id === c.id)
              .map((b) => <option key={b.id} value={b.id}>　└ {b.name}</option>),
          ])}
        </select>
      </label>
      {withStore && q.company && (
        <label className="ctl">
          <span className="k">門市</span>
          <select value={q.store} aria-label="門市" onChange={(e) => q.set({ store: e.target.value || null })}>
            <option value="">全部門市</option>
            {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            <option value="unallocated">公司層級（未分攤）</option>
          </select>
        </label>
      )}
    </>
  );
}
