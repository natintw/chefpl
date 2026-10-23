"""新增管理類別：E14 交通費、E15 設備費、E16 系統租用費（營運費用）。

- E14 交通費：人員交通（計程車、高鐵、停車）；送貨的運費歸 E13
- E15 設備費：買設備當期認列費用。金額大、使用多年的設備應改記 B01 資本支出（按月折舊）
- E16 系統租用費：POS、點餐、雲端系統等月租；影印機、地墊等實體設備租賃仍歸 E05

Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NEW = [("E14", "交通費", 1), ("E15", "設備費", 2), ("E16", "系統租用費", 3)]
KEYWORDS = {"E14": ["計程車", "車資", "高鐵", "停車費"]}


def upgrade() -> None:
    for code, name, offset in NEW:
        op.execute(f"""
            INSERT INTO ledger.mgmt_categories (code, name, pl_section, nature, sort_order)
            SELECT '{code}', '{name}', 'opex', 'expense',
                   COALESCE((SELECT sort_order FROM ledger.mgmt_categories WHERE code = 'E13'), 335) + {offset}
            ON CONFLICT (code) DO NOTHING
        """)
    values = ",\n".join(f"('global_keyword', '{k}', '{code}', 'seed')" for code, kws in KEYWORDS.items() for k in kws)
    op.execute(f"INSERT INTO ledger.classification_rules (layer, keyword, category_code, created_by) VALUES\n{values}")


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _, _ in NEW)
    op.execute(f"DELETE FROM ledger.classification_rules WHERE category_code IN ({codes}) AND created_by = 'seed'")
    op.execute(f"UPDATE ledger.counterparties SET default_category_code = NULL WHERE default_category_code IN ({codes})")
    op.execute(f"DELETE FROM ledger.mgmt_categories WHERE code IN ({codes})")
