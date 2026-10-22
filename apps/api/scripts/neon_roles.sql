-- Neon 帳號與權限【DBA】
-- 1. 在 Neon Console 建立資料庫 ledger，以及兩個 role：ledger_owner、ledger_app（Console → Roles）
-- 2. 用 ledger_owner 的 direct 連線執行 migration：alembic upgrade head
-- 3. 用 ledger_owner 執行本檔，讓 ledger_app 只有讀寫資料的權限、不能改結構

GRANT USAGE ON SCHEMA ledger, guard TO ledger_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ledger, guard TO ledger_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ledger, guard TO ledger_app;

-- 之後 migration 新增的表也自動授權
ALTER DEFAULT PRIVILEGES FOR ROLE ledger_owner IN SCHEMA ledger, guard
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ledger_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ledger_owner IN SCHEMA ledger, guard
  GRANT USAGE, SELECT ON SEQUENCES TO ledger_app;

-- 稽核紀錄只寫不改
REVOKE UPDATE, DELETE ON guard.audit_log FROM ledger_app;
