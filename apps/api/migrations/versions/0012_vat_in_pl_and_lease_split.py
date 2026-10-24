"""Bill 2026-10-24 決定：

- B06 營業稅改為入損益（營運費用），不再列「不入損益」。代碼不變，已記在 B06 的明細從此計入損益表。
- 現金流「租賃融資」只放店租、租車 → O01 租金；融資還款另外放在現金流「借款融資（撥入／還款）」→ B02 借款本金。
- 進貨付款維持預設 C07（廠商設過類別時以廠商為準）。

Revision ID: 0012
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE ledger.mgmt_categories SET pl_section = 'opex', nature = 'expense',
               sort_order = COALESCE((SELECT sort_order FROM ledger.mgmt_categories WHERE code = 'E18'), 340)
        WHERE code = 'B06'
    """)
    op.execute("""
        UPDATE ledger.cash_category_map SET cash_name = '租金（店租、租車）', ledger_category_code = 'O01',
               note = '店租、租車；融資還款請歸到現金流「借款融資」'
        WHERE cash_code = 'EXP_LEA' AND updated_by = 'seed'
    """)
    op.execute("""
        UPDATE ledger.cash_category_map SET note = '營業稅（入損益）；營所稅、牌照稅、房屋稅可改 E18'
        WHERE cash_code = 'EXP_TAX' AND updated_by = 'seed'
    """)
    op.execute("""
        UPDATE ledger.cash_category_map SET cash_name = '借款融資（撥入／還款）',
               note = '借款、融資的撥入與還款，不入損益；利息另記 N01'
        WHERE cash_code = 'FIN_LOAN' AND updated_by = 'seed'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE ledger.mgmt_categories SET pl_section = 'balance', nature = 'balance',
               sort_order = COALESCE((SELECT sort_order FROM ledger.mgmt_categories WHERE code = 'B05'), 470) + 10
        WHERE code = 'B06'
    """)
