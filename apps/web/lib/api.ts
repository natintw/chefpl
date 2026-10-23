"use client";
/**
 * API client。所有請求都打同網域的 /api/v1/*，由 next.config.mjs 轉給後端。
 * 錯誤一律轉成 ApiError（code、message、details 對應後端的錯誤碼表）。
 */
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public details: { field?: string; message: string }[] = []) {
    super(message);
  }
}

const DEV_USER_KEY = "ledger.devUser";

export function getDevUser(): string | null {
  try { return localStorage.getItem(DEV_USER_KEY); } catch { return null; }
}
export function setDevUser(email: string | null) {
  try { email ? localStorage.setItem(DEV_USER_KEY, email) : localStorage.removeItem(DEV_USER_KEY); } catch { /* 無痕視窗 */ }
}

function headers(extra?: HeadersInit): HeadersInit {
  const h: Record<string, string> = {};
  const dev = getDevUser();
  if (dev) h["X-Dev-User"] = dev;               // 後端 AUTH_MODE=dev 時切換測試使用者
  // TODO（工程師）：AUTH_MODE=google 時，改為帶 Google ID token：Authorization: Bearer <id_token>
  return { ...h, ...(extra as Record<string, string>) };
}

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (r.status === 204 ? undefined : await r.json()) as T;
  let body: { code?: string; message?: string; details?: { field?: string; message: string }[] } = {};
  try { body = await r.json(); } catch { /* 非 JSON */ }
  const fallback = r.status >= 500 ? "系統暫時無法服務，請稍後再試" : `請求失敗（${r.status}）`;
  throw new ApiError(r.status, body.code || `HTTP_${r.status}`, body.message || fallback, body.details || []);
}

function qs(params?: Record<string, unknown>): string {
  if (!params) return "";
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, String(v)); });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return handle<T>(await fetch(`/api/v1${path}${qs(params)}`, { headers: headers(), cache: "no-store" }));
}

export async function apiSend<T>(method: "POST" | "PATCH", path: string, body?: unknown): Promise<T> {
  return handle<T>(await fetch(`/api/v1${path}`, {
    method, headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body ?? {}),
  }));
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  return handle<T>(await fetch(`/api/v1${path}`, { method: "POST", headers: headers(), body: form }));
}

/** 下載檔案（帶登入標頭），存成 filename。 */
export async function apiDownload(path: string, filename: string): Promise<void> {
  const res = await fetch(`/api/v1${path}`, { headers: headers(), cache: "no-store" });
  if (!res.ok) await handle(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 讀取資料的 hook：{ data, error, loading, reload }。key 改變時重新讀取。 */
export function useApi<T>(path: string | null, params?: Record<string, unknown>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const key = path ? path + qs(params) : null;
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const d = await apiGet<T>(path, params);
      if (my === seq.current) setData(d);
    } catch (e) {
      if (my === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", "連不到伺服器，請檢查網路"));
    } finally {
      if (my === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load };
}
