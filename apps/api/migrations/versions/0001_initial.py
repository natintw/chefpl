"""初始結構：ledger 與 guard 兩個 schema（對應開發文件 8.3、8.4）

與文件 v0.3 的差異（實作時補上，已回寫文件）：
- mgmt_categories.nature：income / expense / balance，決定金額方向
- settlements.bank_amount：銀行交易實際金額（合併付款、匯費差額）
- recurring_templates.last_amount：折舊最後一期吸收尾差
- import_batches.parsed：解析後、尚未入帳的列
- job_runs：排程執行紀錄

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS guard;

-- ===== 科目與類別 =====
CREATE TABLE ledger.accounts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN
                ('revenue','cogs','opex','non_operating_income','non_operating_expense','balance_sheet')),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE ledger.mgmt_categories (
  code        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  pl_section  TEXT NOT NULL CHECK (pl_section IN
                ('revenue','cogs','labor','occupancy','opex','channel','allocation','non_operating','balance')),
  nature      TEXT NOT NULL CHECK (nature IN ('income','expense','balance')),
  sort_order  INT NOT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK ((pl_section = 'balance') = (nature = 'balance'))
);

CREATE TABLE ledger.category_account_map (
  category_code TEXT PRIMARY KEY REFERENCES ledger.mgmt_categories(code),
  account_id    UUID NOT NULL REFERENCES ledger.accounts(id)
);

-- ===== 對象 =====
CREATE TABLE ledger.counterparties (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                    TEXT NOT NULL,
  tax_id                  CHAR(8),
  id_masked               TEXT,
  roles                   TEXT[] NOT NULL DEFAULT '{vendor}',
  is_intercompany         BOOLEAN NOT NULL DEFAULT FALSE,
  intercompany_company_id TEXT,
  default_category_code   TEXT REFERENCES ledger.mgmt_categories(code),
  payment_rule            TEXT NOT NULL DEFAULT 'next_month_30',
  keywords                TEXT[] NOT NULL DEFAULT '{}',
  issues_invoice          BOOLEAN NOT NULL DEFAULT TRUE,
  is_active               BOOLEAN NOT NULL DEFAULT TRUE,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX counterparties_tax_id ON ledger.counterparties (tax_id) WHERE tax_id IS NOT NULL;
CREATE UNIQUE INDEX counterparties_name_no_tax ON ledger.counterparties (name) WHERE tax_id IS NULL;

CREATE TABLE ledger.classification_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  layer           TEXT NOT NULL CHECK (layer IN ('manual','vendor_keyword','global_keyword')),
  counterparty_id UUID REFERENCES ledger.counterparties(id),
  keyword         TEXT,
  category_code   TEXT NOT NULL REFERENCES ledger.mgmt_categories(code),
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== 單據與明細 =====
CREATE TABLE ledger.documents (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_type         TEXT NOT NULL CHECK (doc_type IN
                     ('payable','receivable','accrual','advance_receipt','journal')),
  source_kind      TEXT NOT NULL CHECK (source_kind IN
                     ('invoice','ap_sheet','payroll','pos','platform','b2b','recurring',
                      'bank_only','allocation','manual','accrual','advance','system','opening')),
  source_ref       TEXT,
  import_batch_id  UUID,
  company_id       TEXT NOT NULL,
  counterparty_id  UUID REFERENCES ledger.counterparties(id),
  doc_date         DATE NOT NULL,
  invoice_nos      TEXT[] NOT NULL DEFAULT '{}',
  has_invoice      BOOLEAN NOT NULL DEFAULT FALSE,
  tax_amount       INT NOT NULL DEFAULT 0,
  settle_amount    INT,
  settled_amount   INT NOT NULL DEFAULT 0,
  settle_status    TEXT NOT NULL DEFAULT 'open' CHECK (settle_status IN
                     ('not_applicable','open','partial','settled','over')),
  settled_by       TEXT CHECK (settled_by IN ('bank','petty_cash','offset')),
  due_date         DATE,
  status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','void')),
  accrual_mode     TEXT CHECK (accrual_mode IN ('estimate_reverse','provision')),
  accrual_template_id UUID,
  expected_delivery_date DATE,
  recognized_amount INT NOT NULL DEFAULT 0,
  related_document_id UUID REFERENCES ledger.documents(id),
  relation         TEXT CHECK (relation IN
                     ('reverses_accrual','consumes_provision','recognizes_advance','depreciates_asset','same_as')),
  late_for_period  CHAR(7),
  memo             TEXT,
  attachment_url   TEXT,
  void_reason      TEXT,
  created_by       TEXT NOT NULL,
  posted_at        TIMESTAMPTZ,
  version          INT NOT NULL DEFAULT 1,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX documents_unique_source
  ON ledger.documents (source_kind, source_ref)
  WHERE source_ref IS NOT NULL AND status <> 'void';
CREATE INDEX documents_open_items ON ledger.documents (company_id, settle_status, due_date);
CREATE INDEX documents_updated ON ledger.documents (updated_at, id);
CREATE INDEX documents_related ON ledger.documents (related_document_id);
CREATE INDEX documents_type_company ON ledger.documents (doc_type, company_id);

CREATE TABLE ledger.document_lines (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id      UUID NOT NULL REFERENCES ledger.documents(id) ON DELETE CASCADE,
  line_no          INT NOT NULL,
  company_id       TEXT NOT NULL,
  store_id         TEXT,
  period_key       CHAR(7) NOT NULL,
  category_code    TEXT NOT NULL REFERENCES ledger.mgmt_categories(code),
  amount           INT NOT NULL,
  is_intercompany  BOOLEAN NOT NULL DEFAULT FALSE,
  description      TEXT,
  UNIQUE (document_id, line_no)
);
CREATE INDEX lines_company_period ON ledger.document_lines (company_id, period_key);
CREATE INDEX lines_store_period ON ledger.document_lines (store_id, period_key);
CREATE INDEX lines_category_period ON ledger.document_lines (category_code, period_key);

CREATE TABLE ledger.settlements (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id     UUID NOT NULL REFERENCES ledger.documents(id),
  cash_txn_id     TEXT NOT NULL,
  cash_account_id TEXT NOT NULL,
  txn_date        DATE NOT NULL,
  amount          INT NOT NULL,
  bank_amount     INT,
  match_level     TEXT NOT NULL CHECK (match_level IN ('exact','fee_diff','partial','over','amount_only','manual')),
  reversed_at     TIMESTAMPTZ,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX settlements_active ON ledger.settlements (document_id, cash_txn_id) WHERE reversed_at IS NULL;
CREATE INDEX settlements_txn ON ledger.settlements (cash_txn_id);

-- ===== 匯入、固定費用、預估、分攤 =====
CREATE TABLE ledger.import_batches (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind          TEXT NOT NULL,
  company_id    TEXT NOT NULL,
  period_key    CHAR(7),
  file_name     TEXT NOT NULL,
  file_url      TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('parsed','committed','failed','superseded')),
  row_count     INT,
  total_amount  INT,
  warnings      JSONB NOT NULL DEFAULT '[]',
  errors        JSONB NOT NULL DEFAULT '[]',
  parsed        JSONB NOT NULL DEFAULT '[]',
  superseded_by UUID REFERENCES ledger.import_batches(id),
  imported_by   TEXT NOT NULL,
  imported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  committed_at  TIMESTAMPTZ
);

CREATE TABLE ledger.recurring_templates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      TEXT NOT NULL,
  store_id        TEXT,
  counterparty_id UUID REFERENCES ledger.counterparties(id),
  category_code   TEXT NOT NULL REFERENCES ledger.mgmt_categories(code),
  doc_type        TEXT NOT NULL CHECK (doc_type IN ('payable','journal')),
  offset_category_code TEXT REFERENCES ledger.mgmt_categories(code),
  related_document_id  UUID REFERENCES ledger.documents(id),
  amount          INT NOT NULL,
  last_amount     INT,
  has_invoice     BOOLEAN NOT NULL DEFAULT FALSE,
  due_day         INT CHECK (due_day BETWEEN 1 AND 31),
  start_period    CHAR(7) NOT NULL,
  end_period      CHAR(7),
  description     TEXT NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE ledger.accrual_templates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      TEXT NOT NULL,
  store_id        TEXT,
  counterparty_id UUID REFERENCES ledger.counterparties(id),
  category_code   TEXT NOT NULL REFERENCES ledger.mgmt_categories(code),
  accrual_mode    TEXT NOT NULL CHECK (accrual_mode IN ('estimate_reverse','provision')),
  estimate_method TEXT NOT NULL DEFAULT 'avg_lookback' CHECK (estimate_method IN ('avg_lookback','fixed')),
  fixed_amount    INT,
  start_period    CHAR(7) NOT NULL,
  end_period      CHAR(7),
  description     TEXT NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE ledger.allocation_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      TEXT NOT NULL,
  name            TEXT NOT NULL,
  category_codes  TEXT[] NOT NULL,
  basis           TEXT NOT NULL CHECK (basis IN ('revenue','fixed','headcount','area')),
  fixed_weights   JSONB,
  effective_from  CHAR(7) NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE ledger.allocation_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   TEXT NOT NULL,
  period_key   CHAR(7) NOT NULL,
  rule_ids     UUID[] NOT NULL,
  document_id  UUID REFERENCES ledger.documents(id),
  basis_values JSONB NOT NULL,
  run_by       TEXT NOT NULL,
  run_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== 月結、待確認、同步、冪等 =====
CREATE TABLE ledger.periods (
  company_id      TEXT NOT NULL,
  period_key      CHAR(7) NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closing','closed')),
  closed_by       TEXT,
  closed_at       TIMESTAMPTZ,
  reopen_reason   TEXT,
  PRIMARY KEY (company_id, period_key)
);

CREATE TABLE ledger.review_items (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        TEXT NOT NULL CHECK (kind IN
                ('unclassified','duplicate_source','new_counterparty','bank_only',
                 'amount_only_match','intercompany_mismatch','late_document',
                 'invoice_changed_after_settle','recurring_ending','import_warning',
                 'accrual_match','provision_leftover','advance_due','capex_life_needed',
                 'intercompany_fund_mismatch','b99_open')),
  company_id  TEXT NOT NULL,
  period_key  CHAR(7),
  ref         TEXT,
  payload     JSONB NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
  resolution  JSONB,
  resolved_by TEXT,
  resolved_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX review_items_open_unique ON ledger.review_items (kind, ref) WHERE status = 'open';

CREATE TABLE ledger.sync_cursors (
  name        TEXT PRIMARY KEY,
  cursor      TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger.integration_requests (
  idempotency_key TEXT NOT NULL,
  client          TEXT NOT NULL,
  endpoint        TEXT NOT NULL,
  body_sha256     TEXT NOT NULL,
  response_status INT NOT NULL,
  response_body   JSONB NOT NULL,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (client, idempotency_key)
);

CREATE TABLE ledger.job_runs (
  id          BIGSERIAL PRIMARY KEY,
  job         TEXT NOT NULL,
  started_at  TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  summary     JSONB NOT NULL DEFAULT '{}'
);

-- 損益與餘額一律經過這兩個 view，避免漏掉 B 類過濾（開發文件 8.4）
CREATE VIEW ledger.v_pl_lines AS
  SELECT l.*, c.pl_section, c.nature, d.doc_type, d.source_kind, d.doc_date
  FROM ledger.document_lines l
  JOIN ledger.documents d ON d.id = l.document_id AND d.status = 'posted'
  JOIN ledger.mgmt_categories c ON c.code = l.category_code
  WHERE c.pl_section <> 'balance';

CREATE VIEW ledger.v_balance_lines AS
  SELECT l.*, c.pl_section, d.doc_type, d.source_kind, d.doc_date, d.counterparty_id
  FROM ledger.document_lines l
  JOIN ledger.documents d ON d.id = l.document_id AND d.status = 'posted'
  JOIN ledger.mgmt_categories c ON c.code = l.category_code
  WHERE c.pl_section = 'balance';

-- ===== 守門層 =====
CREATE TABLE guard.users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email      TEXT NOT NULL UNIQUE,
  name       TEXT NOT NULL,
  role       TEXT NOT NULL CHECK (role IN ('admin','accountant','store_manager')),
  is_active  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE guard.user_scopes (
  user_id    UUID NOT NULL REFERENCES guard.users(id),
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('all','company','store')),
  scope_id   TEXT NOT NULL DEFAULT '*',
  PRIMARY KEY (user_id, scope_kind, scope_id)
);

CREATE TABLE guard.service_clients (
  id            TEXT PRIMARY KEY,
  key_sha256    TEXT[] NOT NULL,
  allow         TEXT[] NOT NULL,
  signing_secret_ref TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE guard.audit_log (
  id          BIGSERIAL PRIMARY KEY,
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_email TEXT NOT NULL,
  action      TEXT NOT NULL,
  resource    TEXT NOT NULL,
  before      JSONB,
  after       JSONB,
  ip          INET
);
CREATE INDEX audit_log_at ON guard.audit_log (at);
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ledger CASCADE; DROP SCHEMA IF EXISTS guard CASCADE;")
