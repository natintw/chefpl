"""發票系統「廠商應付彙總表」：documents.source_kind 增加 invoice_summary；廠商別名

- documents.source_kind 允許 invoice_summary（一列＝公司＋門市＋廠商＋發票月份＋入帳月份）
- counterparties.aliases：同一統編在發票辨識裡出現過的其他寫法（OCR 誤差），只做顯示與名稱比對

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

KINDS = ("invoice", "ap_sheet", "payroll", "pos", "platform", "b2b", "recurring", "bank_only", "allocation",
         "manual", "accrual", "advance", "system", "opening")


def _check(kinds):
    return "source_kind IN (" + ",".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.documents DROP CONSTRAINT IF EXISTS documents_source_kind_check")
    op.execute(f"ALTER TABLE ledger.documents ADD CONSTRAINT documents_source_kind_check "
               f"CHECK ({_check(KINDS + ('invoice_summary',))})")
    op.execute("ALTER TABLE ledger.counterparties ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.counterparties DROP COLUMN IF EXISTS aliases")
    op.execute("ALTER TABLE ledger.documents DROP CONSTRAINT IF EXISTS documents_source_kind_check")
    op.execute(f"ALTER TABLE ledger.documents ADD CONSTRAINT documents_source_kind_check CHECK ({_check(KINDS)})")
