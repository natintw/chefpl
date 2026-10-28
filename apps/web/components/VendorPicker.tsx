"use client";
/**
 * 廠商搜尋（開發文件 5.16）：輸入 8 碼數字用統編找，輸入文字用名稱、簡稱、付款關鍵字找。
 * 名稱相近的廠商會一起列出、各自顯示統編，避免選錯。全系統共用：預付、折讓、代付、一般支出。
 * 資料：GET /vendors/search?q=&company_id=
 */
import { useEffect, useId, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import { money } from "@/lib/format";
import type { VendorHit } from "@/lib/types";
import { Tag } from "./ui";

export function VendorPicker({ label = "廠商（輸入統編或名稱搜尋）", value, onChange, companyId, required, hint }: {
  label?: string; value: VendorHit | null; onChange: (v: VendorHit | null) => void; companyId?: string;
  required?: boolean; hint?: string;
}) {
  const [q, setQ] = useState(value ? value.name : "");
  const [items, setItems] = useState<VendorHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const seq = useRef(0);

  useEffect(() => { if (value) setQ(value.name); }, [value]);
  useEffect(() => {
    if (!open || !q.trim() || (value && q === value.name)) { setItems([]); return; }
    const my = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await apiGet<{ items: VendorHit[] }>("/vendors/search", { q: q.trim(), company_id: companyId, limit: 8 });
        if (my === seq.current) { setItems(r.items); setActive(0); }
      } catch { /* 搜尋失敗不擋登打 */ }
    }, 200);
    return () => clearTimeout(t);
  }, [q, open, companyId, value]);

  function pick(v: VendorHit) { onChange(v); setQ(v.name); setOpen(false); }

  return (
    <div className="f" style={{ position: "relative" }}>
      <label htmlFor={`${listId}-in`}><span>{label}{required ? "" : "（選填）"}</span></label>
      <input id={`${listId}-in`} role="combobox" aria-expanded={open && items.length > 0} aria-controls={listId}
             aria-autocomplete="list" autoComplete="off" value={q} placeholder="例：23344294 或 富統"
             onChange={(e) => { setQ(e.target.value); setOpen(true); if (value) onChange(null); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
             onKeyDown={(e) => {
               if (!items.length) return;
               if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
               if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
               if (e.key === "Enter") { e.preventDefault(); pick(items[active]); }
               if (e.key === "Escape") setOpen(false);
             }} />
      {value ? (
        <span className="small">
          {value.tax_id ? <>統編 <span className="num">{value.tax_id}</span></> : value.id_masked ? <>個人 <span className="num">{value.id_masked}</span></> : "沒有統編"}
          {value.is_intercompany && "・集團內"}
          {value.open_prepayment_amount > 0 && <>・有未沖抵預付 <span className="num">{money(value.open_prepayment_amount)}</span></>}
        </span>
      ) : <span className="small">{hint || "8 碼數字用統編找，文字用名稱找"}</span>}
      {open && items.length > 0 && (
        <ul id={listId} role="listbox" aria-label="符合的廠商" className="card"
            style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20, margin: "4px 0 0", padding: 4,
                     listStyle: "none", boxShadow: "0 6px 18px rgba(0,0,0,.12)", maxHeight: 320, overflowY: "auto" }}>
          {items.map((v, i) => (
            <li key={v.id} role="option" aria-selected={i === active}
                onMouseDown={(e) => { e.preventDefault(); pick(v); }} onMouseEnter={() => setActive(i)}
                style={{ display: "flex", flexWrap: "wrap", gap: "2px 10px", alignItems: "center", padding: "8px 10px",
                         minHeight: 44, borderRadius: 6, cursor: "pointer", background: i === active ? "var(--accent-soft)" : undefined }}>
              <span style={{ fontWeight: 500 }}>{v.name}</span>
              <span className="num small">{v.tax_id || v.id_masked || "沒有統編"}</span>
              {v.is_intercompany && <Tag>集團內</Tag>}
              {v.open_prepayment_amount > 0 && <Tag kind="warn">未沖抵預付 {money(v.open_prepayment_amount)}</Tag>}
              {v.default_category_code && <span className="small" style={{ marginLeft: "auto" }}>預設 {v.default_category_code}</span>}
            </li>
          ))}
          {items[0]?.similar_names && <li className="small" style={{ padding: "6px 10px" }}>名稱相近的廠商有好幾家，請看統編確認</li>}
        </ul>
      )}
    </div>
  );
}
