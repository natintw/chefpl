"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { ReactNode, Suspense } from "react";
import { useApi } from "@/lib/api";
import { DirectoryProvider, useDirectory } from "./directory";
import { ToastProvider } from "./ui";

const NAV: { group: string; items: { href: string; label: string; badge?: boolean; roles?: string[] }[] }[] = [
  { group: "損益", items: [{ href: "/", label: "總覽" }, { href: "/pl", label: "損益表" }] },
  { group: "帳務", items: [
    { href: "/todo", label: "會計待辦", roles: ["admin", "accountant"] },
    { href: "/payables", label: "應付帳款" },
    { href: "/documents/new", label: "新增支出／收入", roles: ["admin", "accountant"] },
  ] },
  { group: "作業", items: [
    { href: "/imports", label: "匯入中心", roles: ["admin", "accountant"] },
    { href: "/review", label: "待確認", badge: true, roles: ["admin", "accountant"] },
    { href: "/close", label: "月結", roles: ["admin", "accountant"] },
  ] },
];

function Side() {
  const pathname = usePathname();
  const sp = useSearchParams();
  const { me } = useDirectory();
  const role = me?.role;
  const canReview = role === "admin" || role === "accountant";
  const reviews = useApi<{ items: unknown[] }>(canReview ? "/review-items" : null);
  const keep = new URLSearchParams();
  ["period", "company", "store"].forEach((k) => { const v = sp.get(k); if (v) keep.set(k, v); });
  const suffix = keep.toString() ? `?${keep}` : "";
  return (
    <aside className="side">
      <div className="brand">帳務損益</div>
      <div className="org">博竑・鄧師傅 集團</div>
      <nav className="nav" aria-label="主選單">
        {NAV.map((g) => (
          <div key={g.group} style={{ display: "contents" }}>
            <div className="grp">{g.group}</div>
            {g.items.filter((i) => !i.roles || (role && i.roles.includes(role))).map((i) => {
              const on = i.href === "/" ? pathname === "/" : pathname.startsWith(i.href);
              return (
                <Link key={i.href} href={`${i.href}${suffix}`} className={on ? "on" : ""} aria-current={on ? "page" : undefined}>
                  {i.label}
                  {i.badge && reviews.data && reviews.data.items.length > 0 && <span className="cnt">{reviews.data.items.length}</span>}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      {me && <div className="small" style={{ padding: "18px 10px 0" }}>{me.name}・{({ admin: "管理者", accountant: "會計", store_manager: "店長" })[me.role]}</div>}
    </aside>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <DirectoryProvider>
        <div className="app">
          <Suspense fallback={<aside className="side" />}><Side /></Suspense>
          <main className="main"><Suspense fallback={null}>{children}</Suspense></main>
        </div>
      </DirectoryProvider>
    </ToastProvider>
  );
}

export function PageHeader({ crumb, title, sub, children }: { crumb: string; title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <header className="top">
      <div>
        <div className="crumb">{crumb}</div>
        <h1>{title}</h1>
        {sub && <div className="sub" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      {children && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{children}</div>}
    </header>
  );
}
