# 帳務損益系統（ledger-desk）

博竑・鄧師傅集團的權責基礎帳務系統：記錄應付、應收、預估入帳、預收款與 B 類（不入損益）項目，產出集團／公司／門市月損益表，並把未結清項目交給現金流系統（ChefCashFlow）核對銀行簿子。

- 開發文件：`docs/帳務損益系統_開發文件.md`（架構、資料模型、規則；本 repo 依 v0.3 實作）
- 對接合約：`contracts/integration-cashflow.yaml`、`contracts/integration-invoice.yaml`
- 對接與部署說明：`docs/系統對接說明_Railway.md`

目前進度：**第 1 期後端 MVP**（API、帳務核心、匯入、對接、排程、測試）。前端（Next.js 帳務台）、POS／外送平台匯入、總部分攤、Excel 匯出排在下一步。

---

## 目錄

```text
ledger-desk/
├─ apps/api/                 # Python 3.12 / FastAPI / SQLAlchemy 2 / Alembic
│  ├─ app/
│  │  ├─ entry/              # 入口：路由、請求格式（不放業務邏輯）
│  │  ├─ guard/              # 守門：登入、權限、系統金鑰、稽核（schema guard）
│  │  ├─ feature/            # 功能：過帳、分類、損益、餘額、預估、預收、匯入、同步、月結（schema ledger）
│  │  │  └─ repositories/    # 唯一讀寫資料庫的地方
│  │  ├─ seams/              # 交接縫：發票系統、現金流主檔、AI、檔案（介面＋Mock＋真實實作）
│  │  ├─ main.py             # API 進入點
│  │  └─ worker.py           # 排程進入點（Railway Cron）
│  ├─ migrations/            # 0001 結構、0002 類別與關鍵字種子
│  ├─ fixtures/              # Mock 用的公司、門市、銀行帳戶、發票事件
│  ├─ scripts/neon_roles.sql # Neon 帳號權限
│  ├─ scripts/seed_demo.py   # 示範資料（只在本機／staging 用）
│  ├─ tests/                 # pytest（27 個案例）
│  ├─ Dockerfile
│  └─ .env.example
├─ apps/web/                 # Next.js 15 帳務台（8 個頁面＋單據抽屜，對應 Design 畫布 S1–S9）
│  ├─ app/                   # /、/pl、/todo、/payables、/documents/new、/imports、/review、/close
│  ├─ components/            # Shell、篩選器、單據抽屜、共用元件（Tag、Skeleton、Empty、Toast…）
│  └─ lib/                   # API client（useApi）、型別、金額格式
├─ contracts/                # 與其他系統的合約（OpenAPI 3.1）
└─ docs/                     # 開發文件、對接說明
```

---

## 本機啟動（不需要任何外部帳號）

```bash
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # 預設 USE_MOCKS=true、AUTH_MODE=dev

# 本機 PostgreSQL（或直接用一個 Neon 分支）
export DATABASE_URL=postgresql://postgres@localhost:5432/ledger
alembic upgrade head

uvicorn app.main:app --reload --port 8080
# 互動文件：http://localhost:8080/docs
# dev 模式以 X-Dev-User 標頭切換使用者，預設 bill@chefteng.com（admin）
```

測試：

```bash
export TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/ledger_test
pytest
```

示範資料（兩個月的單據、一次匯入、幾筆現金流回寫；金額全是虛構）：

```bash
# 伺服器的 INTEGRATION_CLIENTS 裡放 cashflow 金鑰 demo-key 的 SHA-256，才會一併建立結清與銀行獨有項目
API=http://localhost:8080 CASHFLOW_KEY=demo-key python scripts/seed_demo.py 2026-09
python -m app.worker sync-invoices
```

前端：

```bash
cd apps/web
npm install
LEDGER_API_URL=http://localhost:8080 npm run dev     # http://localhost:3000
```

前端所有 `/api/*` 請求由 Next.js 轉給 `LEDGER_API_URL`，瀏覽器只跟同一個網域講話，不需要 CORS。dev 模式下可在瀏覽器 console 執行 `localStorage.setItem("ledger.devUser", "某人@chefteng.com")` 切換使用者。

---

## 資料庫：Neon

1. **建專案**：Neon Console 建立專案，區域選 **AWS Asia Pacific (Singapore)**，與 Railway 的 Singapore 區域同區，延遲最低。
2. **建資料庫與帳號**：資料庫 `ledger`；role 兩個——`ledger_owner`（建表用）、`ledger_app`（應用程式用）。
3. **跑 migration**：用 `ledger_owner` 的 **direct** 連線字串（主機名稱不含 `-pooler`）
   ```bash
   DATABASE_URL_DIRECT='postgresql://ledger_owner:…@ep-xxxx.ap-southeast-1.aws.neon.tech/ledger?sslmode=require' alembic upgrade head
   ```
4. **授權**：用 `ledger_owner` 執行 `apps/api/scripts/neon_roles.sql`。
5. **應用程式連線**：`DATABASE_URL` 用 `ledger_app` 的 **pooled** 連線字串（主機名稱含 `-pooler`）。
   程式已關閉 psycopg 的自動 prepared statements，與 Neon 的 PgBouncer（transaction mode）相容。
6. **staging**：在 Neon 建一個 `staging` 分支，給 Railway 的 staging 環境用；正式資料不會被測試弄髒。

Neon 閒置時會自動暫停，第一個請求可能多幾百毫秒，屬正常現象；連線池設定了 `pool_pre_ping` 處理被回收的連線。

---

## 部署到 Render

兩個 Web Service，都從 `natintw/chefpl` 建立，Region 選 **Singapore**。Render 免費／入門方案沒有私有網路，所以後端會有公開網址，靠兩道鎖保護：前端的密碼門（`SITE_PASSWORD`）、後端只收前端代理的請求（`FRONTEND_PROXY_KEY`）。

**`ledger`（後端）**：Language 選 Docker，Root Directory `apps/api`（Dockerfile Path 維持 `./Dockerfile`），Health Check Path `/healthz`。

```text
DATABASE_URL=（Neon pooled，主機名稱有 -pooler）
DATABASE_URL_DIRECT=（Neon direct，去掉 -pooler）
RUN_MIGRATIONS=true          # 啟動前跑 alembic；付費方案可改用 Pre-Deploy Command：alembic upgrade head
APP_ENV=staging
USE_MOCKS=true
AUTH_MODE=dev
FRONTEND_PROXY_KEY=（一長串亂碼，跟前端 LEDGER_PROXY_KEY 一樣）
```

**`ledger-web`（前端）**：Language 選 Node，Root Directory `apps/web`，Build Command `npm ci && npm run build`，Start Command `npm start`，Health Check Path 留空（密碼門會讓 `/` 回 401）。

```text
LEDGER_API_URL=https://（ledger 服務的 onrender.com 網址，不要結尾斜線）
LEDGER_PROXY_KEY=（同上那串亂碼）
SITE_PASSWORD=（登入帳務台用；帳號 chefteng）
```

`LEDGER_API_URL` 在 build 時寫入，改了要重新部署（Manual Deploy → Clear build cache & deploy）。

與現金流、發票系統對接時（它們在 Railway），跨平台只能走公開網址：`ledger` 要設 `INTEGRATION_MODE=public` 並啟用 HMAC 簽章（見《系統對接說明》的公開網址模式）。

---

## 部署到 Railway

同一個 Railway project（與發票系統、ChefCashFlow 同一個，才能走私有網路），區域選 Singapore。以本 repo 建立以下服務，**Root Directory 都設為 `apps/api`**，Railway 會用 `Dockerfile` 建置：

| 服務名稱 | 啟動指令（Custom Start Command） | Cron（UTC） | 其他設定 |
| --- | --- | --- | --- |
| `ledger` | （使用 Dockerfile 預設） | — | 變數 `HOST=::`（私有網路要 IPv6）；Pre-deploy：`alembic upgrade head`；Healthcheck：`/healthz` |
| `ledger-worker-sync` | `python -m app.worker sync-invoices` | `5-59/15 * * * *` | 每 15 分鐘，錯開發票系統 |
| `ledger-worker-monthly` | `python -m app.worker run-recurring` | `5 0 1 * *` | 台北每月 1 日 08:05 |
| `ledger-worker-directory` | `python -m app.worker refresh-directory` | `40 * * * *` | 每小時 |
| `ledger-files` | （Storage Bucket） | — | 匯入原檔、收據照片 |

另外建一個前端服務：

| 服務名稱 | Root Directory | Build | Start | 變數 |
| --- | --- | --- | --- | --- |
| `ledger-web` | `apps/web` | `npm run build`（Railpack 自動偵測） | `npm start` | `LEDGER_API_URL=http://ledger.railway.internal:8080`（build 時寫入，改了要重新部署）、`SITE_PASSWORD=…`；Healthcheck `/`；產生公開網域 |

前端透過私有網路呼叫 `ledger`，所以 `ledger` 本身**不必**開公開網域；只要給 ChefCashFlow 與發票系統走私有網路即可。若暫時需要公開 `ledger` 的 `/docs` 來除錯，看完就關掉。

**`AUTH_MODE=dev` 時任何進得了前端的人都是管理者**。所以在換成 Google 登入之前，`ledger-web` 一定要設 `SITE_PASSWORD`（帳號 `SITE_USER`，預設 `chefteng`）：瀏覽器會先跳出帳密視窗，頁面和 `/api/*` 都擋住。換成 Google 登入後再拿掉這兩個變數（兩者都用 Authorization 標頭，不能同時開）。

**服務名稱 `ledger` 不要改**：它就是私有網路的主機名稱 `ledger.railway.internal`，ChefCashFlow 照合約呼叫這個位址。

Pre-deploy 的 `alembic upgrade head` 需要 `DATABASE_URL_DIRECT`（ledger_owner）；其餘服務只需要 `DATABASE_URL`（ledger_app）。

**環境變數**：見 `apps/api/.env.example`，每行都有註解。建議把共用的值設成 Railway 的 Shared Variables，四個服務一起引用。正式環境至少要改：

```text
APP_ENV=production
USE_MOCKS=false
AUTH_MODE=google
GOOGLE_OAUTH_CLIENT_ID=…
DATABASE_URL=…（Neon pooled, ledger_app）
DATABASE_URL_DIRECT=…（Neon direct, ledger_owner；只有 ledger 服務需要）
INVOICE_API_KEY、CASHFLOW_API_KEY、INTEGRATION_CLIENTS（見《系統對接說明》第 3 節）
FILE_STORE_MODE=s3 與 BUCKET 等五個變數（${{ledger-files.*}}）
```

上游系統還沒準備好時，可以單獨把 `COMPANY_DIRECTORY_MODE=mock` 或 `USE_MOCKS=true` 留著，系統照樣能用。

---

## 發票彙總表與廠商類別

發票系統匯出的「廠商應付彙總表」直接在「匯入中心 → 發票彙總表」上傳（選成「應付帳款表」也會自動辨識）。

- 一個檔案涵蓋所有公司：每列依「公司統編」分到公司（公司主檔要有統編），依「入帳月份」記帳，「預計付款日」當到期日。
- 一列＝公司＋門市＋廠商＋發票月份＋入帳月份。同一列再匯入：金額相同略過、金額不同以新的取代（已付款的不能取代）。
- 含稅金額依 5% 還原未稅；廠商統編欄是身分證字號的（個人）視為無發票，號碼遮蔽後才存。
- 類別來自「廠商類別」對應表（以統一編號為準，存在 counterparties.default_category_code）。沒有對應的列先記草稿，
  設定類別後自動補入帳。對應表可以在頁面上逐筆設定，或下載 Excel 整批填好再上傳；上傳時統編相同、名稱不同，
  以 Excel 的名稱為準，發票辨識的其他寫法留作別名。

【待確認】發票系統每次匯出的是「當月全部」還是「只有新上傳的」：目前以「當月全部」處理（金額變了就取代）。
若是只匯出新增的，同一廠商同月份第二次匯出會蓋掉第一次，需改成累加。

---

## 匯入範本（目前版本）

【待確認】真實的應付表、薪資表格式。拿到樣本後只需調整 `app/feature/imports/parsers.py` 的欄位別名。

**應付表（kind=ap_sheet）**：第一列可放標題，系統會自己找表頭。

| 門市 | 代號 | 廠商 | 發票號碼 | 摘要 | 應付金額 |
| --- | --- | --- | --- | --- | --- |
| 文衡店 | 42799631 | 博竑食品 | AB11111111 | 8月貨款 | 462228 |
| | | 成本合計 | | | … |
| 文衡店 | | 德昇會計師事務所 | AB44444444 | 7+8月記帳費 | 10000 |

- 「成本合計」以上是成本、以下是費用（沿用《分類規則》1.1）。
- 自動處理：一格多張發票、「沒發票」、身分證遮蔽、「7+8月」拆期、新廠商、集團內交易；規則判斷不了的列先入草稿並進待確認。
- 同一公司同一月份重新匯入：舊批次自動作廢（已付款的除外）。

**薪資表（kind=payroll）**：每門市一列，欄位「門市、薪資、加班與獎金、勞保雇主、健保雇主、勞退」；門市填「公司層級」代表總部人事（待分攤）。只匯入門市彙總，不存個人薪資。

---

## 主要 API

完整清單見 `/docs`（Swagger UI）。

| 用途 | 端點 |
| --- | --- |
| 損益總覽、損益表、下鑽 | `GET /api/v1/pl/overview`、`/pl/statement`、`/pl/drilldown` |
| 單據 | `GET/POST /api/v1/documents`、`/documents/check`、`PATCH /documents/{id}`、`/void`、`/link-accrual` |
| 預估、預收 | `GET /api/v1/accruals/suggestions`、`POST /accruals/batch`、`GET /advances`、`POST /advances/recognize` |
| B 類餘額 | `GET /api/v1/balances` |
| 匯入 | `POST /api/v1/imports` → `GET /imports/{id}` → `POST /imports/{id}/commit` |
| 待確認、月結 | `GET /api/v1/review-items`、`POST /review-items/{id}/resolve`、`/periods/{company}/{period}`、`/close`、`/reopen` |
| 設定 | `/api/v1/settings/categories`、`counterparties`、`rules`、`recurring`、`accrual-templates` |
| 現金流系統 | `GET /integration/v1/open-items`、`POST /settlements`、`POST /bank-only-items`、`GET /balances` |

---

## 與開發文件 v0.3 的差異（實作時補上）

- `mgmt_categories.nature`（income／expense／balance）決定金額方向；`settlements.bank_amount`；`recurring_templates.last_amount`（折舊尾差）；`import_batches.parsed`；`job_runs`。
- 新增類別 `E12 銀行手續費`、`N03 雜項收入`、`N04 雜項支出`（文件原為「N01–N03 利息、補助、雜項」）。
- 分公司與總公司視為同一法人：分公司的應付可由總公司帳戶付款（文衡分公司 ↔ 名勝食品 3578）。
- 集團層級損益：所有標記為集團內的 R06／C99 都沖銷，兩邊不相等的差額列為 `intercompany_unmatched`。

## 下一步

1. 前端 Google 登入（目前 dev 模式以 `X-Dev-User` 切換使用者）；匯入解析器依實際應付表、薪資表格式調整
2. POS（EAT365）日報、外送平台、博竑銷貨（b2b_sales）、期初開帳（opening_balance）parser
3. 總部分攤（allocation_rules／runs）、Excel 匯出
4. 會計師科目表對應（category_account_map）
