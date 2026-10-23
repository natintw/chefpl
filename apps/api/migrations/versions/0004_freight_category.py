"""新增管理類別 E13 運費物流（營運費用），加上摘要關鍵字。

貨運、宅配、物流、冷鏈倉儲的費用原本只能暫列 E08 勞務費。

Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

KEYWORDS = ["運費", "物流", "宅配", "貨運", "快遞"]


def upgrade() -> None:
    op.execute("""
        INSERT INTO ledger.mgmt_categories (code, name, pl_section, nature, sort_order)
        SELECT 'E13', '運費物流', 'opex', 'expense', COALESCE((SELECT sort_order FROM ledger.mgmt_categories
                                                              WHERE code = 'E12'), 330) + 5
        ON CONFLICT (code) DO NOTHING
    """)
    values = ",\n".join(f"('global_keyword', '{k}', 'E13', 'seed')" for k in KEYWORDS)
    op.execute(f"INSERT INTO ledger.classification_rules (layer, keyword, category_code, created_by) VALUES\n{values}")


def downgrade() -> None:
    op.execute("DELETE FROM ledger.classification_rules WHERE category_code = 'E13' AND created_by = 'seed'")
    op.execute("UPDATE ledger.counterparties SET default_category_code = NULL WHERE default_category_code = 'E13'")
    op.execute("DELETE FROM ledger.mgmt_categories WHERE code = 'E13'")
