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

export type Overview = { period: string; totals: Kpis & { revenue: number; gross_profit: number;
  store_operating_profit: number; net_profit: number }; rows: OverviewRow[]; unallocated_amount: number;
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
  description: string | null; is_intercompany: boolean };
export type Doc = {
  id: string; doc_type: string; source_kind: string; company_id: string;
  counterparty: { id: string; name: string; is_intercompany: boolean } | null;
  doc_date: string; invoice_nos: string[]; has_invoice: boolean; settle_amount: number | null; settled_amount: number;
  tax_amount: number; outstanding: number | null; settle_status: string; settled_by: string | null; due_date: string | null;
  status: string; memo: string | null; store_ids: string[]; period_keys: string[]; version: number;
  lines?: DocLine[]; settlements?: { cash_txn_id: string; cash_account_id: string; txn_date: string; amount: number;
    match_level: string; reversed: boolean }[];
};

export type Category = { code: string; name: string; pl_section: string; nature: string; is_active: boolean };
export type Counterparty = { id: string; name: string; tax_id: string | null; default_category_code: string | null;
  payment_rule: string; keywords: string[]; is_intercompany: boolean };
export type ReviewItem = { id: string; kind: string; company_id: string; period: string | null; ref: string | null;
  payload: Record<string, any>; created_at: string };
export type ChecklistItem = { code: string; label: string; status: "passed" | "failed" | "na"; note: string };
export type Finding = { code: string; level: "ok" | "warn" | "info" | "error"; message: string; data: Record<string, any> };
