"""所有設定都從環境變數讀取（Railway 的 Variables）。每個欄位的用途見 .env.example。"""
import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ===== 執行環境 =====
    app_env: str = "local"
    use_mocks: bool = True
    port: int = 8080

    # ===== 資料庫（Neon）=====
    # 應用程式用 Neon 的 pooled 連線（主機名稱含 -pooler）；migration 用 direct 連線
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/ledger"
    database_url_direct: str | None = None
    guard_database_url: str | None = None  # 未設定時與 database_url 相同
    db_pool_size: int = 5

    # ===== 跨系統 =====
    integration_mode: str = "private"  # private / public
    invoice_base_url: str = "http://invoice.railway.internal:8080"
    invoice_api_key: str = ""
    invoice_sync_batch: int = 200
    company_directory_mode: str = "mock"  # mock / cashflow_api
    cashflow_base_url: str = "http://cashflow.railway.internal:8080"
    cashflow_api_key: str = ""
    integration_clients: str = "[]"  # JSON；見《系統對接說明》3.3
    integration_signing_secret: str = ""
    http_read_timeout_sec: float = 10
    http_write_timeout_sec: float = 30

    # ===== 帳務規則 =====
    tax_rate: float = 0.05
    amount_tolerance: int = 1
    default_payment_rule: str = "next_month_30"
    close_day_of_month: int = 10
    late_document_policy: str = "next_period"
    capex_threshold: int = 80000
    accrual_lookback_months: int = 3
    opening_date: str = "2026-11-01"
    fee_diff_tolerance: int = 100
    # 現金流系統的這些分類對不到單據時不進待確認，標「不需核對」（現金存提、內部調度、更正調整）
    bank_quiet_categories: str = "CSH_WDL,CSH_DEP,ADJ_COR,INT_TRN,INT_REV"

    # ===== AI =====
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5"
    ai_prompt_version: str = "v1"
    ai_timeout_sec: float = 15
    ai_min_confidence: float = 0.70

    # ===== 檔案（Railway Storage Bucket）=====
    file_store_mode: str = "local"  # local / s3
    local_file_dir: str = "./.files"
    bucket: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    endpoint: str = ""
    region: str = "auto"
    signed_url_ttl_sec: int = 600
    max_upload_mb: int = 20

    # ===== 認證 =====
    auth_mode: str = "dev"  # dev / google
    google_oauth_client_id: str = ""
    allowed_email_domain: str = "chefteng.com"
    dev_default_user: str = "bill@chefteng.com"
    # 後端有公開網址時（例如 Render 免費方案沒有私有網路），設這個值：/api/* 必須帶相同的 X-Proxy-Key，
    # 也就是只有我們的前端代理進得來。前端那邊設 LEDGER_PROXY_KEY 為同一個值。
    frontend_proxy_key: str = ""
    bootstrap_admins: str = Field(default="bill@chefteng.com", description="逗號分隔；第一次登入自動建立為 admin")

    @field_validator("database_url", "database_url_direct", "guard_database_url", mode="before")
    @classmethod
    def _psycopg_driver(cls, v: str | None) -> str | None:
        """Neon 給的是 postgresql://…；SQLAlchemy 要 postgresql+psycopg://…，這裡自動轉換，直接貼上即可。
        也容忍常見的貼上格式：Neon 複製按鈕的 psql '…'、前後引號與空白、誤把「DATABASE_URL=」一起貼進來。"""
        if isinstance(v, str):
            v = v.strip()
            m = re.search(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"]+", v)
            if m:
                v = m.group(0)
            for prefix in ("postgres://", "postgresql://"):
                if v.startswith(prefix):
                    return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def migration_url(self) -> str:
        """優先用 direct；direct 沒設或不是合法連線字串時退回 DATABASE_URL（並在 log 說明）。"""
        if self.database_url_direct and _parseable(self.database_url_direct):
            return self.database_url_direct
        if self.database_url_direct:
            print("[config] DATABASE_URL_DIRECT 不是合法的連線字串，遷移改用 DATABASE_URL", flush=True)
        return self.database_url


def _parseable(url: str) -> bool:
    from sqlalchemy.engine import make_url
    try:
        make_url(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def describe_url(url: str | None) -> str:
    """給 log 看的遮罩摘要：只露出格式、主機、資料庫名，不露密碼。"""
    if not url:
        return "（未設定）"
    from sqlalchemy.engine import make_url
    try:
        u = make_url(url)
        return f"格式正確 driver={u.drivername} user={u.username} host={u.host} db={u.database} 密碼={'有' if u.password else '無'}"
    except Exception:  # noqa: BLE001
        flags = {"開頭是 postgres": url.lstrip().lower().startswith(("postgres", "psql")), "含 ://": "://" in url,
                 "含 @": "@" in url, "含中文": any("\u4e00" <= ch <= "\u9fff" for ch in url), "含換行": "\n" in url,
                 "含引號": any(q in url for q in "'\"")}
        return f"無法解析：長度 {len(url)}，" + "，".join(f"{k} {'是' if v else '否'}" for k, v in flags.items())


@lru_cache
def get_settings() -> Settings:
    return Settings()
