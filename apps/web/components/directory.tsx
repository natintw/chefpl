"use client";
/** 公司、門市、使用者：整個前端共用，進站時讀一次。 */
import { createContext, ReactNode, useContext, useMemo } from "react";
import { useApi } from "@/lib/api";
import type { Category, Directory, Me } from "@/lib/types";

type Ctx = {
  me: Me | null; dir: Directory; categories: Category[]; loading: boolean;
  companyName: (id: string | null | undefined) => string; storeName: (id: string | null | undefined) => string;
  categoryName: (code: string) => string; error: string | null;
};
const DirCtx = createContext<Ctx | null>(null);

export function DirectoryProvider({ children }: { children: ReactNode }) {
  const me = useApi<Me>("/me");
  const dir = useApi<Directory>("/directory");
  const cats = useApi<{ items: Category[] }>("/settings/categories");
  const value = useMemo<Ctx>(() => {
    const d = dir.data || { companies: [], stores: [] };
    const cmap = new Map(d.companies.map((c) => [c.id, c.name]));
    const smap = new Map(d.stores.map((s) => [s.id, s.name]));
    const catmap = new Map((cats.data?.items || []).map((c) => [c.code, c.name]));
    return {
      me: me.data, dir: d, categories: cats.data?.items || [],
      loading: me.loading || dir.loading || cats.loading,
      error: me.error?.message || dir.error?.message || null,
      companyName: (id) => (id ? cmap.get(id) || id : "—"),
      storeName: (id) => (id ? smap.get(id) || id : "公司層級"),
      categoryName: (code) => catmap.get(code) || code,
    };
  }, [me.data, me.loading, me.error, dir.data, dir.loading, dir.error, cats.data, cats.loading]);
  return <DirCtx.Provider value={value}>{children}</DirCtx.Provider>;
}

export function useDirectory(): Ctx {
  const c = useContext(DirCtx);
  if (!c) throw new Error("DirectoryProvider missing");
  return c;
}
