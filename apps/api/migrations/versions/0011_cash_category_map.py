"""現金流分類 → 損益類別對照表；補齊兩邊對不上的類別。

現金流系統（ChefCashFlow）匯入對帳單時已先判斷每筆是「進貨付款、薪資、水電雜支…」，
帳務系統收到後用這張表換成損益類別，當作待確認的預設類別與損益月份（薪資、勞健保預設上個月）。
同一家廠商在「廠商類別」設過類別時，以廠商類別為準（較細，例如進貨付款裡的肉品／蔬果）。

新增損益類別：E17 電信網路、E18 稅捐規費、N05 利息收入（現金流系統原本就有、帳務系統缺的）。

Revision ID: 0011
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

NEW_CATS = [("E17", "電信網路", "opex", "expense", "E16", 1), ("E18", "稅捐規費", "opex", "expense", "E16", 2),
            ("N05", "利息收入", "non_operating", "income", "N04", 1)]
KEYWORDS = {"E17": ["電信", "電話費", "網路費"], "E18": ["牌照稅", "房屋稅", "地價稅", "規費"]}

# (現金流代碼, 現金流名稱, 損益類別, 損益月份 same/prev, 說明)
SEED = [
    ("REV_OP", "營業收入", None, "same", "門市營收以 POS 匯入為準；刷卡、QR 入帳是沖應收，不另記營收"),
    ("REV_INT", "利息收入", "N05", "same", None),
    ("REV_OTH", "其他收入", "N03", "same", None),
    ("EXP_PUR", "進貨付款", "C07", "same", "廠商設過類別時以廠商類別為準（肉品、蔬果…）"),
    ("EXP_SAL", "薪資", "P01", "prev", "月初匯的是上個月薪資"),
    ("EXP_SOC", "人事社保", "P03", "prev", "勞健保費是繳上個月的；勞退請改 P04"),
    ("EXP_LEA", "租賃融資", "O01", "same", "【待確認】現金流把店租、租車、融資還款放在一起；車輛設備改 E05、融資本金改 B02"),
    ("EXP_UTI", "水電雜支", "O02", "same", None),
    ("EXP_TEL", "電信費", "E17", "same", None),
    ("EXP_TAX", "稅費", "B06", "same", "多數是營業稅（不入損益）；營所稅、牌照稅、房屋稅請改 E18"),
    ("EXP_NOT", "票據支付", None, "same", "看不出付給誰，需人工判斷"),
    ("EXP_OTH", "其他支出", None, "same", "需人工判斷"),
    ("EXP_FEE", "銀行手續費", "E12", "same", None),
    ("EXP_INT", "利息支出", "N01", "same", None),
    ("FIN_LOAN", "借款撥入／還本", "B02", "same", "不入損益"),
    ("CSH_WDL", "現金提領", None, "same", "不需核對"),
    ("CSH_DEP", "現金存入", None, "same", "不需核對"),
    ("INT_TRN", "內部調度", None, "same", "集團內部，不送帳務系統"),
    ("INT_REV", "營業收入(關聯匯入)", None, "same", "集團內部，不送帳務系統"),
    ("ADJ_COR", "更正調整", None, "same", "不需核對"),
    ("UNCAT", "未歸類", None, "same", "現金流系統還沒分類"),
]


def _q(s):
    return "NULL" if s is None else "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    for code, name, sec, nat, after, off in NEW_CATS:
        op.execute(f"""
            INSERT INTO ledger.mgmt_categories (code, name, pl_section, nature, sort_order)
            SELECT '{code}', '{name}', '{sec}', '{nat}',
                   COALESCE((SELECT max(sort_order) FROM ledger.mgmt_categories WHERE code = '{after}'), 500) + {off}
            ON CONFLICT (code) DO NOTHING
        """)
    values = ",\n".join(f"('global_keyword', '{k}', '{c}', 'seed')" for c, kws in KEYWORDS.items() for k in kws)
    op.execute(f"INSERT INTO ledger.classification_rules (layer, keyword, category_code, created_by) VALUES\n{values}")
    op.execute("""
        CREATE TABLE ledger.cash_category_map (
          cash_code            TEXT PRIMARY KEY,
          cash_name            TEXT NOT NULL,
          ledger_category_code TEXT REFERENCES ledger.mgmt_categories(code),
          period_mode          TEXT NOT NULL DEFAULT 'same' CHECK (period_mode IN ('same', 'prev')),
          note                 TEXT,
          updated_by           TEXT NOT NULL DEFAULT 'seed',
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    rows = ",\n".join(f"({_q(c)}, {_q(n)}, {_q(l)}, {_q(p)}, {_q(note)})" for c, n, l, p, note in SEED)
    op.execute("INSERT INTO ledger.cash_category_map (cash_code, cash_name, ledger_category_code, period_mode, note) "
               f"VALUES\n{rows}")


def downgrade() -> None:
    op.execute("DROP TABLE ledger.cash_category_map")
    codes = ", ".join(f"'{c[0]}'" for c in NEW_CATS)
    op.execute(f"DELETE FROM ledger.classification_rules WHERE category_code IN ({codes}) AND created_by = 'seed'")
    op.execute(f"UPDATE ledger.counterparties SET default_category_code = NULL WHERE default_category_code IN ({codes})")
    op.execute(f"DELETE FROM ledger.mgmt_categories WHERE code IN ({codes})")
