/** 後端回應的型別（對應 apps/api 的 serializers 與 reports）。 */
export type Company = { id: string; code: string; name: string; tax_id: string | null; parent_company_id: string | null };
export type Store = { id: string; company_id: string; code: string; name: string; kind: string };
export type Directory = { companies: Company[]; stores: Store[] };
export type Me = { email: string; name: string; role: "admin" | "accountant" | "store_manager";
  scope: { all: boolean; companies: string[]; stores: string[] } };

export type Kpis = { food_cost_pct: number | null; labor_cost_pct: number | null; prime_cost_pct: number | null;
  occupancy_pct: number | null; store_margin_pct: number | null };

export type Completeness = { level: "complete" | "partial"; payroll: string; open_review_items: number;
  pending_invoices: number | null; invoice_system_reachable: boolean; missing: string[] };

export type OverviewRow = Kpis & { company: { id: string; name: string }; store: { id: string; name: string };
  revenue: number; store_operating_profit: number; completeness: Completeness };

/** 公司小計：旗下門市＋公司層級（未分攤）。集團內交易在公司層級不沖銷。 */
export type CompanySubtotal = Kpis & { id: string; name: string; parent_company_id: string | null; revenue: number;
  stores_operating_profit: number; unallocated_amount: number; operating_profit: number; net_profit: number };
/** 法人合計：總公司＋旗下分公司。 */
export type EntityTotal = Kpis & { id: string; name: string; company_ids: string[]; revenue: number;
  operating_profit: number; net_profit: number };
export type Overview = { period: string; totals: Kpis & { revenue: number; gross_profit: number;
  store_operating_profit: number; net_profit: number }; rows: OverviewRow[]; companies?: CompanySubtotal[];
  entities?: EntityTotal[];
  unallocated_amount: number;
  intercompany_unmatched: number; accrual_amount: number; advance_balance: number; stores_with_data: number };

export type Statement = {
  period_from: string; period_to: string; columns: { key: string; label: string }[];
  sections: { section: string; label: string; lines: { category_code: string; label: string; values: Record<string, number> }[];
    subtotal: Record<string, number> }[];
  subtotals: Record<"gross_profit" | "store_operating_profit" | "store_net_profit" | "net_profit", Record<string, number>>;
  kpis: Record<string, Kpis>; intercompany_eliminated: { revenue: number; cogs: number } | null;
  intercompany_unmatched: number | null;
};

export type DocLine = { line_no: number; period_key: string; store_id: string | null; category_code: string; amount: number;
  description: string | null; is_intercompany: boolean; counterparty_id?: string | null;
  original_document_id?: string | null; account_override_id?: string | null };
export type Doc = {
  id: string; doc_type: string; source_kind: string; company_id: string;
  counterparty: { id: string; name: string; is_intercompany: boolean } | null;
  doc_date: string; invoice_nos: string[]; has_invoice: boolean; settle_amount: number | null; settled_amount: number;
  tax_amount: number; outstanding: number | null; settle_status: string; settled_by: string | null; due_date: string | null;
  status: string; memo: string | null; store_ids: string[]; period_keys: string[]; version: number;
  settled_dates?: string[]; bank_txn_ids?: string[];
  offset_amount?: number; related_document_id?: string | null; relation?: string | null;
  refund_method?: string | null; allowance_no?: string | null; allowance_reason?: string | null;
  expected_invoice_period?: string | null; order_total?: number | null; on_behalf_of_company_id?: string | null;
  paid_by_company_id?: string | null; invoice_buyer_tax_id?: string | null;
  lines?: DocLine[]; settlements?: { cash_txn_id: string; cash_account_id: string; txn_date: string; amount: number; bank_amount?: number | null;
    match_level: string; reversed: boolean ; kind?: "cash" | "bank"; note?: string | null; created_by?: string | null }[];
};

export type Category = { code: string; name: string; pl_section: string; nature: string; is_active: boolean };
export type Counterparty = { id: string; name: string; tax_id: string | null; default_category_code: string | null;
  payment_rule: string; keywords: string[]; is_intercompany: boolean };
export type ReviewItem = { id: string; kind: string; company_id: string; period: string | null; ref: string | null;
  payload: Record<string, any>; created_at: string; suggested_store_id?: string | null;
  suggested_category_code?: string | null; suggested_period_mode?: "same" | "prev" | null; suggestion_source?: string | null };
export type ChecklistItem = { code: string; label: string; status: "passed" | "failed" | "na"; note: string };
export type Finding = { code: string; level: "ok" | "warn" | "info" | "error"; message: string; data: Record<string, any> };

/** 廠商搜尋結果（GET /vendors/search） */
export type VendorHit = { id: string; name: string; tax_id: string | null; id_masked: string | null; no_tax_id: boolean;
  aliases: string[]; is_intercompany: boolean; intercompany_company_id: string | null;
  default_category_code: string | null; match: "tax_id" | "name" | "keyword"; open_prepayment_amount: number;
  similar_names: boolean };
export type OpenPrepayment = { document_id: string; doc_date: string; remaining: number; amount: number; memo: string | null;
  company_id: string; expected_invoice_period: string | null; planned_category_code: string | null; paid: boolean };
export type Offset = { id: string; kind: "prepayment" | "allowance" | "vendor_credit" | "paid_by_other";
  from_document_id: string; to_document_id: string; amount: number; auto_applied: boolean;
  journal_document_id: string | null; created_at: string | null; created_by: string; reversed_at: string | null };
