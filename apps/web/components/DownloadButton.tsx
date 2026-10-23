"use client";
/** 下載 Excel：帶目前畫面的篩選，存成 filename。 */
import { useState } from "react";
import { useToast } from "./ui";
import { apiDownload } from "@/lib/api";

export function DownloadButton({ path, params, filename, label = "下載 Excel" }: {
  path: string; params: Record<string, string | number | undefined | null>; filename: string; label?: string;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, String(v)); });
    try {
      await apiDownload(`${path}?${q}`, filename);
    } catch (e) {
      toast((e as Error).message || "下載失敗", "error");
    } finally { setBusy(false); }
  }
  return <button className="btn" disabled={busy} onClick={go}>{busy ? "產生中…" : label}</button>;
}
