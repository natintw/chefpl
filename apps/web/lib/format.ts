/** 集團名稱：門市 → 公司 → 集團的最上層。 */
export const GROUP_NAME = "鄧師傅餐飲";

/** 金額：千分位；負數用括號（開發文件 11.5：不只靠顏色）；0 顯示「—」。 */
export function money(n: number | null | undefined, opts: { zeroDash?: boolean } = {}): string {
  if (n === null || n === undefined) return "—";
  if (n === 0 && opts.zeroDash !== false) return "—";
  const s = Math.abs(Math.round(n)).toLocaleString("en-US");
  return n < 0 ? `(${s})` : s;
}

export function pct(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${n.toFixed(1)}%`;
}

export function delta(n: number): string {
  if (n === 0) return "0";
  return n > 0 ? `+${money(n)}` : money(n);
}

export function addMonths(period: string, n: number): string {
  const [y, m] = period.split("-").map(Number);
  const idx = y * 12 + (m - 1) + n;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

/** 預設看上個月（開發文件 D12：本月資料一定不完整）。 */
export function defaultPeriod(now = new Date()): string {
  const tz = new Date(now.getTime() + 8 * 3600 * 1000); // 台北時間
  const cur = `${tz.getUTCFullYear()}-${String(tz.getUTCMonth() + 1).padStart(2, "0")}`;
  return addMonths(cur, -1);
}

export const PRIME_WARN = 65; // 【待確認】KPI 警戒值，目前為示意

export const SOURCE_LABEL: Record<string, string> = {
  invoice: "發票", invoice_summary: "發票彙總", ap_sheet: "應付表", payroll: "薪資", pos: "POS", platform: "平台", b2b: "B2B",
  recurring: "固定", bank_only: "銀行", allocation: "分攤", manual: "人工", accrual: "預估",
  advance: "預收", system: "系統", opening: "開帳",
};

export const DOC_TYPE_LABEL: Record<string, string> = {
  payable: "應付", receivable: "應收", accrual: "預估", advance_receipt: "預收", journal: "調整",
  prepayment: "預付款", allowance: "折讓",
};

export const SETTLE_LABEL: Record<string, [string, string]> = {
  open: ["未付", "t-neg"], partial: ["部分付款", "t-warn"], settled: ["已付", "t-ok"], over: ["溢付", "t-warn"],
  not_applicable: ["不適用", "t-est"],
};

/** 應收、預收用「收」，其他用「付」 */
export function settleLabel(status: string, docType?: string): [string, string] {
  const [label, cls] = SETTLE_LABEL[status] || [status, "t-est"];
  if (docType === "allowance") return status === "settled" ? ["已沖抵", "t-ok"] : status === "open" ? ["廠商欠我們", "t-warn"] : [label, cls];
  return docType === "receivable" || docType === "advance_receipt" ? [label.replace("付", "收"), cls] : [label, cls];
}

export const REVIEW_LABEL: Record<string, string> = {
  bank_only: "銀行有、單據無", unclassified: "分類", duplicate_source: "重複來源", amount_only_match: "只有金額相符",
  intercompany_mismatch: "集團內不一致", new_counterparty: "新對象", invoice_changed_after_settle: "發票已變更",
  recurring_ending: "固定費用到期", b99_open: "B99 待改歸", capex_life_needed: "資本支出", advance_due: "預收到期",
  accrual_match: "預估對應", provision_leftover: "預提剩餘", late_document: "晚到單據", import_warning: "匯入警告",
  intercompany_fund_mismatch: "集團內資金不一致",
  prepayment_match: "待沖抵預付", prepayment_overdue: "預付逾期未沖抵", ap_sheet_netted: "應付表已扣訂金",
  allowance_unmatched: "折讓找不到原單", invoice_buyer_mismatch: "發票抬頭不符", vendor_tax_id_missing: "廠商缺統編",
};
