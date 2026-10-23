"""廠商的銀行帳號（遮蔽後）：銀行交易比對時「先看對方帳號」。

webhook 用名稱比對成功一次後，系統就記住對方帳號，下次直接用帳號對。

Revision ID: 0006
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.counterparties ADD COLUMN IF NOT EXISTS bank_accounts TEXT[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.counterparties DROP COLUMN IF EXISTS bank_accounts")
