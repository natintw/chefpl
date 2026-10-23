"""對象的預設門市：例如員工屬於哪家門市。從銀行交易建立薪資單據時選過門市，下次自動帶出。

Revision ID: 0010
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.counterparties ADD COLUMN IF NOT EXISTS default_store_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.counterparties DROP COLUMN IF EXISTS default_store_id")
