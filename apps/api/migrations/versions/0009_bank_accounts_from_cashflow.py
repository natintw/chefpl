"""銀行帳戶以現金流系統為準：webhook 每筆交易帶上帳戶所屬公司、銀行、名稱，帳務系統記在這張表。

Bill 在現金流系統新增、修改帳戶（換所屬公司、改名）後，下一次送交易就會更新；
不必再手動維護 fixtures/bank_accounts.json（那份只當作還沒收到交易前的預設）。

Revision ID: 0009
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE IF NOT EXISTS ledger.bank_accounts (
        id          TEXT PRIMARY KEY,
        company_id  TEXT NOT NULL,
        bank_name   TEXT,
        alias       TEXT,
        last4       TEXT,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
      )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ledger.bank_accounts")
