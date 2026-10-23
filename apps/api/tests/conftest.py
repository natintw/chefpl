"""測試環境：本機 PostgreSQL（TEST_DATABASE_URL），所有交接縫用 Mock。

  TEST_DATABASE_URL=postgresql+psycopg://postgres@/ledger_test?host=/tmp&port=54329 pytest
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

TEST_KEY = "sk_cashflow_ledger_test_key_0123456789abcdef"
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@/ledger_test?host=/tmp&port=54329")

os.environ.update({
    "APP_ENV": "test", "USE_MOCKS": "true", "AUTH_MODE": "dev", "DATABASE_URL": TEST_DB,
    "LOCAL_FILE_DIR": "/tmp/ledger-test-files",
    "INTEGRATION_CLIENTS": json.dumps([{
        "client": "cashflow", "key_sha256": [hashlib.sha256(TEST_KEY.encode()).hexdigest()],
        "allow": ["GET /integration/v1/open-items", "GET /integration/v1/balances",
                  "POST /integration/v1/settlements", "POST /integration/v1/bank-only-items",
                  "POST /integration/v1/webhooks/bank-transactions"]}]),
})

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine, session_scope  # noqa: E402
from app.seams import invoice_feed  # noqa: E402

KEEP = {"mgmt_categories", "classification_rules", "alembic_version", "cash_category_map"}
MS = "comp-1790263124662"          # 名勝食品
WH = "comp-1790322196308"          # 名勝文衡分公司
WH_STORE = "store-msw-01"
ACC_3578 = "acc-1790323488187"     # 名勝3578貨款用


@pytest.fixture(scope="session", autouse=True)
def _schema():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["url"] = TEST_DB
    with engine().begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS ledger CASCADE; DROP SCHEMA IF EXISTS guard CASCADE;"
                       "DROP TABLE IF EXISTS public.alembic_version;"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    with engine().begin() as c:
        rows = c.execute(text("SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('ledger','guard')"))
        # counterparties 被種子規則表參照，不能 TRUNCATE CASCADE（會連種子關鍵字一起清掉）
        tables = [f"{s}.{t}" for s, t in rows if t not in KEEP | {"counterparties"}]
        c.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
        c.execute(text("DELETE FROM ledger.classification_rules WHERE created_by <> 'seed'"))
        c.execute(text("DELETE FROM ledger.counterparties"))
        c.execute(text("DELETE FROM ledger.cash_category_map WHERE updated_by <> 'seed'"))
    invoice_feed.set_invoice_feed(invoice_feed.MockInvoiceFeed())
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def svc_headers():
    return {"Authorization": f"Bearer {TEST_KEY}"}


@pytest.fixture
def session():
    with session_scope() as s:
        yield s
