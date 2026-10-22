"""種子資料：管理類別（開發文件 6.1、3.6）與全域摘要關鍵字（《分類規則》3.3、開發文件 5.4）

R、P、O、E10–E12、A、N、B 為開發文件提出的草案【待確認】；確認後若有調整，新增 migration 修改，不改本檔。

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# (代碼, 名稱, pl_section, nature)
CATEGORIES = [
    ("R01", "內用", "revenue", "income"),
    ("R02", "外帶", "revenue", "income"),
    ("R03", "外送平台", "revenue", "income"),
    ("R04", "團購／B2B", "revenue", "income"),
    ("R05", "其他營收", "revenue", "income"),
    ("R06", "集團內銷貨", "revenue", "income"),
    ("C01", "蔬果", "cogs", "expense"),
    ("C02", "肉品", "cogs", "expense"),
    ("C03", "水產", "cogs", "expense"),
    ("C04", "飲料酒水", "cogs", "expense"),
    ("C05", "冰品甜點", "cogs", "expense"),
    ("C06", "米糧乾貨", "cogs", "expense"),
    ("C07", "綜合食材", "cogs", "expense"),
    ("C99", "集團內進貨", "cogs", "expense"),
    ("P01", "薪資", "labor", "expense"),
    ("P02", "加班與獎金", "labor", "expense"),
    ("P03", "勞健保雇主負擔", "labor", "expense"),
    ("P04", "勞退提繳", "labor", "expense"),
    ("O01", "租金", "occupancy", "expense"),
    ("O02", "水電瓦斯", "occupancy", "expense"),
    ("O03", "管理費", "occupancy", "expense"),
    ("O04", "保險", "occupancy", "expense"),
    ("O05", "折舊攤提", "occupancy", "expense"),
    ("E01", "修繕保養", "opex", "expense"),
    ("E02", "清潔用品", "opex", "expense"),
    ("E03", "清潔消毒", "opex", "expense"),
    ("E04", "垃圾清運", "opex", "expense"),
    ("E05", "設備租賃", "opex", "expense"),
    ("E06", "包材耗材", "opex", "expense"),
    ("E07", "包裝印刷", "opex", "expense"),
    ("E08", "勞務費", "opex", "expense"),
    ("E09", "行銷費用", "opex", "expense"),
    ("E12", "銀行手續費", "opex", "expense"),
    ("E10", "外送平台佣金", "channel", "expense"),
    ("E11", "刷卡手續費", "channel", "expense"),
    ("A01", "總部分攤", "allocation", "expense"),
    ("N01", "利息支出", "non_operating", "expense"),
    ("N02", "補助收入", "non_operating", "income"),
    ("N03", "雜項收入", "non_operating", "income"),
    ("N04", "雜項支出", "non_operating", "expense"),
    ("B01", "資本支出", "balance", "balance"),
    ("B02", "借款本金", "balance", "balance"),
    ("B03", "押金", "balance", "balance"),
    ("B04", "股東往來", "balance", "balance"),
    ("B05", "集團內資金往來", "balance", "balance"),
    ("B06", "營業稅", "balance", "balance"),
    ("B99", "其他（不入損益）", "balance", "balance"),
]

GLOBAL_KEYWORDS = {
    "C01": ["蔬菜", "青菜", "水果", "果"],
    "C02": ["肉", "雞", "豬", "牛", "培根"],
    "C03": ["魚", "蝦", "蟹", "貝", "魷"],
    "C04": ["汽水", "鮮奶", "果汁", "啤酒", "酒", "茶"],
    "C05": ["冰棒", "冰淇淋", "甜點"],
    "C06": ["米", "麵", "乾貨", "菇"],
    "E01": ["維修", "保養"],
    "E02": ["洗碗精", "垃圾袋", "抹布", "清潔劑", "除油劑", "漂白水"],
    "E03": ["消毒"],
    "E04": ["清運"],
    "E05": ["租金"],
    "E06": ["餐盒", "餐巾紙", "提袋", "吸管"],
    "E07": ["腰封", "卡片", "印刷"],
    "E08": ["記帳", "會計", "律師", "顧問"],
    "E12": ["手續費", "匯費"],
    "N01": ["利息"],
    "B02": ["還本", "本金", "攤還", "質借還款", "撥貸", "借款撥入"],
    "B03": ["押金", "保證金"],
    "B06": ["營業稅", "401", "國稅局"],
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    rows = ",\n".join(
        f"({_q(c)}, {_q(n)}, {_q(sec)}, {_q(nat)}, {i * 10})" for i, (c, n, sec, nat) in enumerate(CATEGORIES)
    )
    op.execute(f"INSERT INTO ledger.mgmt_categories (code, name, pl_section, nature, sort_order) VALUES\n{rows};")
    kw_rows = ",\n".join(
        f"('global_keyword', {_q(k)}, {_q(code)}, 'seed')"
        for code, kws in GLOBAL_KEYWORDS.items() for k in kws
    )
    op.execute(
        "INSERT INTO ledger.classification_rules (layer, keyword, category_code, created_by) VALUES\n"
        f"{kw_rows};"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ledger.classification_rules WHERE created_by = 'seed';")
    op.execute("DELETE FROM ledger.mgmt_categories;")
