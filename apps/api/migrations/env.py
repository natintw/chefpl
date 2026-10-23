"""Alembic 環境：使用 Neon 的 direct 連線（DATABASE_URL_DIRECT），未設定時退回 DATABASE_URL。"""
from alembic import context
from sqlalchemy import create_engine, pool

from app.config import describe_url, get_settings

config = context.config


def run_migrations_online() -> None:
    st = get_settings()
    if not config.attributes.get("url"):
        print(f"[migrate] DATABASE_URL：{describe_url(st.database_url)}", flush=True)
        print(f"[migrate] DATABASE_URL_DIRECT：{describe_url(st.database_url_direct)}", flush=True)
    url = config.attributes.get("url") or st.migration_url
    log = (lambda m: print(f"[migrate] {m}", flush=True)) if not config.attributes.get("url") else (lambda m: None)
    # 連線最多等 15 秒；等鎖最多 30 秒（有別的連線卡著同一張表時，直接失敗並說明，不要無聲卡住整個部署）
    connectable = create_engine(url, poolclass=pool.NullPool,
                                connect_args={"prepare_threshold": None, "connect_timeout": 15})
    log("連線資料庫中…")
    with connectable.connect() as connection:
        connection.exec_driver_sql("SET lock_timeout = '30s'")
        connection.exec_driver_sql("SET statement_timeout = '10min'")
        connection.commit()
        try:
            cur = connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar()
        except Exception:
            cur = None
        connection.rollback()  # 結束這個讀取用的交易，migration 才會由 alembic 自己逐版 commit
        log(f"已連線，目前版本 {cur or '（空資料庫）'}")

        def on_apply(ctx, step, heads, run_args):  # noqa: ANN001
            log(f"完成 {step.up_revision_id if hasattr(step, 'up_revision_id') else step}")

        context.configure(
            connection=connection,
            version_table="alembic_version",
            version_table_schema="public",
            transaction_per_migration=True,
            on_version_apply=on_apply,
        )
        try:
            with context.begin_transaction():
                context.run_migrations()
        except Exception as e:  # noqa: BLE001
            msg = str(getattr(e, "orig", e)).splitlines()[0]
            if "lock timeout" in msg or "canceling statement due to lock timeout" in msg:
                log("失敗：等資料表鎖超過 30 秒。有其他連線正在使用同一張表（例如 Neon SQL 編輯器開著交易、"
                    "或上一次部署的程序還沒結束）。請在 Neon 執行「SELECT pid, state, query FROM pg_stat_activity」"
                    "找出 idle in transaction 的連線並結束它，再重新部署。")
            else:
                log(f"失敗：{msg}")
            raise
        final = connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar()
        connection.rollback()
        log(f"完成，資料庫版本 {final}")


run_migrations_online()
