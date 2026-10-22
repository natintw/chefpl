"use client";
/** 共用元件（Component Inventory，開發文件 11.6）。每個元件都考慮 loading／empty／error。 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";

export function Tag({ kind = "est", children }: { kind?: "est" | "ok" | "warn" | "neg" | "src"; children: ReactNode }) {
  return <span className={`tag t-${kind}`}>{children}</span>;
}

export function Skeleton({ rows = 4, height = 14 }: { rows?: number; height?: number }) {
  return (
    <div className="stack pad" aria-busy="true" aria-label="載入中">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="sk" style={{ height, width: `${92 - (i % 3) * 14}%` }} />
      ))}
    </div>
  );
}

export function KpiSkeleton({ n = 4 }: { n?: number }) {
  return (
    <div className="grid-kpi" aria-busy="true">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="card pad stack" style={{ gap: 8 }}>
          <div className="sk" style={{ height: 12, width: "55%" }} />
          <div className="sk" style={{ height: 26, width: "80%" }} />
          <div className="sk" style={{ height: 10, width: "45%" }} />
        </div>
      ))}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="t">{title}</div>
      {children && <div className="sub" style={{ margin: "4px 0 14px" }}>{children}</div>}
      {action}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const msg = error.code === "FORBIDDEN" ? error.message : error.code === "NETWORK" ? error.message
    : error.status >= 500 ? "讀不到資料，請稍後重新整理" : error.message;
  return (
    <div className="errbox" role="alert">
      <span style={{ flex: "1 1 240px" }}>{msg}</span>
      {onRetry && <button className="btn sm" onClick={onRetry}>重試</button>}
    </div>
  );
}

export function FieldErrors({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <div className="errbox" role="alert">
      <div>
        <div style={{ fontWeight: 600 }}>{error.message}</div>
        {error.details?.map((d, i) => <div key={i} className="small" style={{ color: "inherit" }}>{d.field ? `${d.field}：` : ""}{d.message}</div>)}
      </div>
    </div>
  );
}

// ----- Toast -----
type ToastMsg = { text: string; kind: "ok" | "error" } | null;
const ToastCtx = createContext<(text: string, kind?: "ok" | "error") => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<ToastMsg>(null);
  const show = useCallback((text: string, kind: "ok" | "error" = "ok") => setMsg({ text, kind }), []);
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 4000);
    return () => clearTimeout(t);
  }, [msg]);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div aria-live="polite">{msg && <div className={`toast ${msg.kind === "error" ? "error" : ""}`}>{msg.text}</div>}</div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ----- Modal -----
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className="card pad modal stack" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ fontSize: 16 }}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Mark({ status }: { status: "passed" | "failed" | "na" | "warn" | "info" | "ok" | "error" }) {
  const map = { passed: ["ok", "✓"], ok: ["ok", "✓"], failed: ["no", "!"], error: ["no", "!"], na: ["na", "–"],
    warn: ["wa", "!"], info: ["in", "i"] } as const;
  const [cls, sym] = map[status];
  return <span className={`mk ${cls}`} aria-hidden="true">{sym}</span>;
}
