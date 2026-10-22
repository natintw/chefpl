"""資料庫連線。

Neon 注意事項：
- 應用程式使用 pooled 連線字串（主機名稱含 `-pooler`），Neon 以 PgBouncer transaction mode 提供連線池。
  transaction mode 不支援 server-side prepared statements，所以 psycopg 要關閉自動 prepare（prepare_threshold=None）。
- migration（Alembic）使用 direct 連線字串（DATABASE_URL_DIRECT），因為 DDL 與 advisory lock 需要完整 session。
- Neon 閒置會自動暫停（scale to zero），第一個請求可能多幾百毫秒；pool_pre_ping 會處理被回收的連線。
- 連線字串需帶 `sslmode=require`。
"""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker] = {}


def _make_engine(url: str) -> Engine:
    s = get_settings()
    return create_engine(
        url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_pool_size,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"prepare_threshold": None},
        future=True,
    )


def engine(kind: str = "ledger") -> Engine:
    """kind = ledger（功能層）或 guard（守門層）。兩者可指向同一個 Neon 資料庫的不同帳號。"""
    if kind not in _engines:
        s = get_settings()
        url = s.guard_database_url if (kind == "guard" and s.guard_database_url) else s.database_url
        _engines[kind] = _make_engine(url)
        _factories[kind] = sessionmaker(bind=_engines[kind], expire_on_commit=False, future=True)
    return _engines[kind]


def session_factory(kind: str = "ledger") -> sessionmaker:
    engine(kind)
    return _factories[kind]


@contextmanager
def session_scope(kind: str = "ledger") -> Iterator[Session]:
    """worker 與服務層使用：成功 commit、失敗 rollback。"""
    session = session_factory(kind)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_ledger_session() -> Iterator[Session]:
    """FastAPI dependency：每個請求一個交易。"""
    with session_scope("ledger") as s:
        yield s


def get_guard_session() -> Iterator[Session]:
    with session_scope("guard") as s:
        yield s


def reset_engines() -> None:
    """測試用：設定改變後重建連線。"""
    for e in _engines.values():
        e.dispose()
    _engines.clear()
    _factories.clear()
