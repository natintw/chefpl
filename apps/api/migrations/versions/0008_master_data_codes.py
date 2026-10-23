"""集團主檔統一（2026-10-23，Bill 確認的對照表）：門市改用新 ID，巨蛋店歸高雄分公司。

公司 ID 不變（與現金流系統共用），只改代碼與名稱（在 fixtures/主檔，不在資料庫）。
門市 ID 改名，所有引用舊 ID 的地方一起改：單據明細、固定費用、預估範本、分攤規則與紀錄、
尚未入帳的匯入預覽、使用者可看的門市範圍。

損益的公司別是看明細的 company_id。門市歸哪家公司，它的明細就算哪家公司
（之後新增的單據由程式自動帶入門市所屬公司），所以把既有明細的 company_id 也對齊門市所屬公司。

Revision ID: 0008
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

RENAMES = {
    "store-bh-judan": "store-bhk-judan",
    "store-bh-dale": "store-bhk-dale",
    "store-bh-hanshin": "store-bhk-hanshin",
    "store-bh-yixiang": "store-bhk-yixiang",
    "store-bh-dreammall": "store-bhk-dreammall",
    "store-bh-counter": "store-bhh-judan",
    "store-ms-main": "store-ms-01",
    "store-ms-wenheng": "store-msw-01",
}
# 門市 → 所屬公司（新 ID）
STORE_COMPANY = {
    "store-bh-factory": "comp-1",
    "store-bhk-judan": "comp-1790263075210",
    "store-bhk-dale": "comp-1790263075210",
    "store-bhk-hanshin": "comp-1790263075210",
    "store-bhk-yixiang": "comp-1790263075210",
    "store-bhk-dreammall": "comp-1790263075210",
    "store-bhh-judan": "comp-bh-hanshin",
    "store-br-yucheng": "comp-borren",
    "store-ct-01": "comp-1790263102815",
    "store-ms-01": "comp-1790263124662",
    "store-msw-01": "comp-1790322196308",
    "store-ys-01": "comp-2",
}
PLAIN = [("ledger.document_lines", "store_id"), ("ledger.recurring_templates", "store_id"),
         ("ledger.accrual_templates", "store_id")]
JSON = [("ledger.allocation_rules", "fixed_weights"), ("ledger.allocation_runs", "basis_values"),
        ("ledger.import_batches", "parsed")]


def _rename(mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        for table, col in PLAIN:
            op.execute(f"UPDATE {table} SET {col} = '{new}' WHERE {col} = '{old}'")
        for table, col in JSON:
            op.execute(f"""UPDATE {table} SET {col} = replace({col}::text, '"{old}"', '"{new}"')::jsonb
                           WHERE {col}::text LIKE '%"{old}"%'""")
        op.execute(f"UPDATE guard.user_scopes SET scope_id = '{new}' WHERE scope_kind = 'store' AND scope_id = '{old}'")


def upgrade() -> None:
    _rename(RENAMES)
    for store, company in STORE_COMPANY.items():
        op.execute(f"UPDATE ledger.document_lines SET company_id = '{company}' "
                   f"WHERE store_id = '{store}' AND company_id <> '{company}'")


def downgrade() -> None:
    _rename({v: k for k, v in RENAMES.items()})
    # 巨蛋店原本算總公司
    op.execute("UPDATE ledger.document_lines SET company_id = 'comp-1' WHERE store_id = 'store-bh-judan'")
