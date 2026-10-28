"""預付款、折讓、代付、沖抵、廠商主檔（開發文件 v0.6：3.2、3.8、5.13–5.17）

- 單據類型新增 prepayment（預付款）、allowance（折讓）
- documents：沖抵累計 offset_amount、預付／折讓／代付相關欄位
- document_offsets：不經過銀行的沖減（預付沖應付、折讓沖原單、他公司代付、廠商欠款抵下次）
- document_lines：counterparty_id（B 類往來對象）、original_document_id（折讓的原單）、account_override_id（指定科目）
- counterparties：no_tax_id（確認沒有統編的個人、小販）
- 類別 B07 預付款、B08 代墊款（不入損益）
- 科目「代付（進項）」「代支出（銷項）」：代付發票抬頭為付款公司時使用（D24）

Revision ID: 0013
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

DOC_TYPES = ("payable", "receivable", "accrual", "advance_receipt", "journal", "prepayment", "allowance")
RELATIONS = ("reverses_accrual", "consumes_provision", "recognizes_advance", "depreciates_asset", "same_as",
             "offsets_prepayment", "allowance_of", "paid_on_behalf")
SETTLED_BY = ("bank", "petty_cash", "offset", "paid_by_other")
REVIEW_KINDS = ("unclassified", "duplicate_source", "new_counterparty", "bank_only", "amount_only_match",
                "intercompany_mismatch", "late_document", "invoice_changed_after_settle", "recurring_ending",
                "import_warning", "accrual_match", "provision_leftover", "advance_due", "capex_life_needed",
                "intercompany_fund_mismatch", "b99_open",
                "prepayment_match", "prepayment_overdue", "ap_sheet_netted", "allowance_unmatched",
                "invoice_buyer_mismatch", "vendor_tax_id_missing")

OLD_DOC_TYPES = DOC_TYPES[:5]
OLD_RELATIONS = RELATIONS[:5]
OLD_SETTLED_BY = SETTLED_BY[:3]
OLD_REVIEW_KINDS = REVIEW_KINDS[:16]


def _in(col: str, vals: tuple[str, ...]) -> str:
    return f"{col} IN (" + ",".join(f"'{v}'" for v in vals) + ")"


def _recheck(table: str, name: str, cond: str) -> None:
    op.execute(f"ALTER TABLE ledger.{table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE ledger.{table} ADD CONSTRAINT {name} CHECK ({cond})")


def upgrade() -> None:
    _recheck("documents", "documents_doc_type_check", _in("doc_type", DOC_TYPES))
    _recheck("documents", "documents_relation_check", _in("relation", RELATIONS))
    _recheck("documents", "documents_settled_by_check", _in("settled_by", SETTLED_BY))
    _recheck("review_items", "review_items_kind_check", _in("kind", REVIEW_KINDS))

    op.execute("""
    ALTER TABLE ledger.documents
      ADD COLUMN IF NOT EXISTS offset_amount INT NOT NULL DEFAULT 0,
      ADD COLUMN IF NOT EXISTS expected_invoice_period CHAR(7),
      ADD COLUMN IF NOT EXISTS planned_category_code TEXT REFERENCES ledger.mgmt_categories(code),
      ADD COLUMN IF NOT EXISTS order_total INT,
      ADD COLUMN IF NOT EXISTS original_invoice_no TEXT,
      ADD COLUMN IF NOT EXISTS allowance_no TEXT,
      ADD COLUMN IF NOT EXISTS allowance_reason TEXT
        CHECK (allowance_reason IN ('return','quality','shortage','price')),
      ADD COLUMN IF NOT EXISTS refund_method TEXT
        CHECK (refund_method IN ('reduce_outstanding','vendor_refund','credit_next')),
      ADD COLUMN IF NOT EXISTS on_behalf_of_company_id TEXT,
      ADD COLUMN IF NOT EXISTS paid_by_company_id TEXT,
      ADD COLUMN IF NOT EXISTS invoice_buyer_tax_id CHAR(8);

    CREATE UNIQUE INDEX IF NOT EXISTS documents_unique_allowance_no
      ON ledger.documents (counterparty_id, allowance_no)
      WHERE doc_type = 'allowance' AND allowance_no IS NOT NULL AND status <> 'void';
    CREATE INDEX IF NOT EXISTS documents_prepayment_open
      ON ledger.documents (company_id, counterparty_id) WHERE doc_type = 'prepayment' AND status = 'posted';
    CREATE INDEX IF NOT EXISTS documents_invoice_nos_gin ON ledger.documents USING gin (invoice_nos);

    ALTER TABLE ledger.document_lines
      ADD COLUMN IF NOT EXISTS counterparty_id UUID REFERENCES ledger.counterparties(id),
      ADD COLUMN IF NOT EXISTS original_document_id UUID REFERENCES ledger.documents(id),
      ADD COLUMN IF NOT EXISTS account_override_id UUID REFERENCES ledger.accounts(id);

    ALTER TABLE ledger.counterparties
      ADD COLUMN IF NOT EXISTS no_tax_id BOOLEAN NOT NULL DEFAULT FALSE;

    CREATE TABLE IF NOT EXISTS ledger.document_offsets (
      id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      kind                TEXT NOT NULL CHECK (kind IN ('prepayment','allowance','paid_by_other','vendor_credit')),
      from_document_id    UUID NOT NULL REFERENCES ledger.documents(id),
      to_document_id      UUID NOT NULL REFERENCES ledger.documents(id),
      amount              INT NOT NULL CHECK (amount > 0),
      journal_document_id UUID REFERENCES ledger.documents(id),
      auto_applied        BOOLEAN NOT NULL DEFAULT FALSE,
      created_by          TEXT NOT NULL,
      created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
      reversed_at         TIMESTAMPTZ,
      reversed_by         TEXT,
      reverse_reason      TEXT,
      CHECK (from_document_id <> to_document_id)
    );
    CREATE INDEX IF NOT EXISTS document_offsets_to ON ledger.document_offsets (to_document_id)
      WHERE reversed_at IS NULL;
    CREATE INDEX IF NOT EXISTS document_offsets_from ON ledger.document_offsets (from_document_id)
      WHERE reversed_at IS NULL;

    -- B 類餘額的往來對象：明細有指定就用明細的（代付時同一張單據的 B05 對象是另一家公司）
    DROP VIEW IF EXISTS ledger.v_balance_lines;
    CREATE VIEW ledger.v_balance_lines AS
      SELECT l.id, l.document_id, l.line_no, l.company_id, l.store_id, l.period_key, l.category_code, l.amount,
             l.is_intercompany, l.description, c.pl_section, d.doc_type, d.source_kind, d.doc_date,
             COALESCE(l.counterparty_id, d.counterparty_id) AS counterparty_id
      FROM ledger.document_lines l
      JOIN ledger.documents d ON d.id = l.document_id AND d.status = 'posted'
      JOIN ledger.mgmt_categories c ON c.code = l.category_code
      WHERE c.pl_section = 'balance';
    """)
    op.execute("""
    INSERT INTO ledger.mgmt_categories (code, name, pl_section, nature, sort_order) VALUES
      ('B07', '預付款', 'balance', 'balance', 445),
      ('B08', '代墊款', 'balance', 'balance', 447)
    ON CONFLICT (code) DO NOTHING;

    -- 代號待德昇科目表確定後更新（D24）
    INSERT INTO ledger.accounts (code, name, kind) VALUES
      ('OB-IN', '代付（進項）', 'balance_sheet'),
      ('OB-OUT', '代支出（銷項）', 'balance_sheet')
    ON CONFLICT (code) DO NOTHING;
    """)
    op.execute("""
    INSERT INTO ledger.classification_rules (layer, keyword, category_code, created_by) VALUES
      ('global_keyword', '訂金', 'B07', 'seed'),
      ('global_keyword', '頭期款', 'B07', 'seed')
    """)


def downgrade() -> None:
    op.execute("""
    DELETE FROM ledger.classification_rules WHERE category_code = 'B07' AND created_by = 'seed';
    DROP VIEW IF EXISTS ledger.v_balance_lines;
    CREATE VIEW ledger.v_balance_lines AS
      SELECT l.id, l.document_id, l.line_no, l.company_id, l.store_id, l.period_key, l.category_code, l.amount,
             l.is_intercompany, l.description, c.pl_section, d.doc_type, d.source_kind, d.doc_date, d.counterparty_id
      FROM ledger.document_lines l
      JOIN ledger.documents d ON d.id = l.document_id AND d.status = 'posted'
      JOIN ledger.mgmt_categories c ON c.code = l.category_code
      WHERE c.pl_section = 'balance';
    DROP TABLE IF EXISTS ledger.document_offsets;
    ALTER TABLE ledger.document_lines DROP COLUMN IF EXISTS counterparty_id,
      DROP COLUMN IF EXISTS original_document_id, DROP COLUMN IF EXISTS account_override_id;
    ALTER TABLE ledger.counterparties DROP COLUMN IF EXISTS no_tax_id;
    DROP INDEX IF EXISTS ledger.documents_unique_allowance_no;
    DROP INDEX IF EXISTS ledger.documents_prepayment_open;
    DROP INDEX IF EXISTS ledger.documents_invoice_nos_gin;
    ALTER TABLE ledger.documents DROP COLUMN IF EXISTS offset_amount, DROP COLUMN IF EXISTS expected_invoice_period,
      DROP COLUMN IF EXISTS planned_category_code, DROP COLUMN IF EXISTS order_total,
      DROP COLUMN IF EXISTS original_invoice_no, DROP COLUMN IF EXISTS allowance_no,
      DROP COLUMN IF EXISTS allowance_reason, DROP COLUMN IF EXISTS refund_method,
      DROP COLUMN IF EXISTS on_behalf_of_company_id, DROP COLUMN IF EXISTS paid_by_company_id,
      DROP COLUMN IF EXISTS invoice_buyer_tax_id;
    DELETE FROM ledger.accounts WHERE code IN ('OB-IN', 'OB-OUT');
    DELETE FROM ledger.mgmt_categories WHERE code IN ('B07', 'B08');
    """)
    _recheck("documents", "documents_doc_type_check", _in("doc_type", OLD_DOC_TYPES))
    _recheck("documents", "documents_relation_check", _in("relation", OLD_RELATIONS))
    _recheck("documents", "documents_settled_by_check", _in("settled_by", OLD_SETTLED_BY))
    _recheck("review_items", "review_items_kind_check", _in("kind", OLD_REVIEW_KINDS))
