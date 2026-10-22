"""Alembic 環境：使用 Neon 的 direct 連線（DATABASE_URL_DIRECT），未設定時退回 DATABASE_URL。"""
from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings

config = context.config


def run_migrations_online() -> None:
    url = config.attributes.get("url") or get_settings().migration_url
    connectable = create_engine(url, poolclass=pool.NullPool, connect_args={"prepare_threshold": None})
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            version_table="alembic_version",
            version_table_schema="public",
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
