"""現金付款：應付用現金付掉（零用金、門市收銀、老闆／員工代墊）時，在帳務系統標成已付。

沿用 settlements 表：cash_txn_id = "cash-<付款 ID>"（一次付款可付好幾張單）、cash_account_id = "cash:<來源>"。
加備註與建立者兩欄，記下誰付的、收據號碼等。

Revision ID: 0007
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.settlements ADD COLUMN IF NOT EXISTS note TEXT")
    op.execute("ALTER TABLE ledger.settlements ADD COLUMN IF NOT EXISTS created_by TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.settlements DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE ledger.settlements DROP COLUMN IF EXISTS note")
