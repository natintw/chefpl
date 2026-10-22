# 三系統對接說明（Railway 部署版）

> 版本：v1.0（草案）　|　日期：2026-10-22　|　文件擁有者：Bill（鄧至佑）
> 對象：ChefCashFlow 工程師、發票系統工程師、帳務損益系統工程師、DevOps
> 搭配檔案：`contracts/integration-cashflow.yaml`、`contracts/integration-invoice.yaml`（OpenAPI 3.1，可直接匯入 Swagger UI、Postman、Stoplight 檢視）

---

## 1. 一張圖看三個系統

```text
                         ┌──────────────────────────┐
  發票系統 invoice ──①──▶ │                          │ ◀──②── 現金流系統 cashflow
                         │  帳務損益系統 ledger      │ ──③──▶（ChefCashFlow）
                         │                          │ ◀──④──
                         └──────────────────────────┘
        │                          │                          ▲
        └───────────── ⑤ 公司、門市、銀行帳戶主檔 ─────────────┘
```

| # | 呼叫方 → 提供方 | 端點 | 頻率 | 合約檔 |
| --- | --- | --- | --- | --- |
| ① | ledger → invoice | `GET /api/v1/integration/invoice-events` | 每 15 分鐘 | integration-invoice.yaml |
| ① | ledger → invoice | `GET /api/v1/integration/pending-count`、`/invoices/{id}/image` | 月結時、使用者點原圖時 | integration-invoice.yaml |
| ② | cashflow → ledger | `GET /integration/v1/open-items` | 每次銀行核對前；每天一次完整同步 | integration-cashflow.yaml |
| ③ | cashflow → ledger | `POST /integration/v1/settlements`、`/bank-only-items` | 每次銀行核對後 | integration-cashflow.yaml |
| ④ | cashflow → ledger | `GET /integration/v1/balances` | 資金預警計算時 | integration-cashflow.yaml |
| ⑤ | ledger、invoice → cashflow | `GET /integration/v1/companies`、`/stores`、`/bank-accounts` | 每小時快取一次 | integration-cashflow.yaml |

三條原則：

1. **發票系統和現金流系統彼此不直接連**，一切經過帳務損益系統。「誰還欠多少」只有一個答案。
2. **誰的資料誰提供**：應付應收屬 ledger，銀行交易屬 cashflow，發票屬 invoice。沒有任何一方讀別人的資料庫。
3. **任何一方停機，其他系統照常運作**，恢復後自動補齊（游標、冪等、重試）。

---

## 2. Railway 部署架構

### 2.1 服務配置

三個系統放在**同一個 Railway project**，分 `production` 與 `staging` 兩個 environment。每個 environment 內：

| Railway 服務名稱 | 內容 | 對外公開 | 私有網路位址 |
| --- | --- | --- | --- |
| `invoice` | 發票系統 API＋前端 | 是（使用者用的畫面） | `invoice.railway.internal:8080` |
| `invoice-worker` | 發票系統排程（掃收件匣） | 否；Cron | — |
| `invoice-db` | PostgreSQL | 否 | `invoice-db.railway.internal:5432` |
| `ledger` | 帳務損益系統 API＋前端 | 是 | `ledger.railway.internal:8080` |
| `ledger-worker` | 帳務損益系統排程 | 否；Cron | — |
| `ledger-db` | PostgreSQL | 否 | `ledger-db.railway.internal:5432` |
| `ledger-files` | Storage Bucket（匯入原檔、收據照片） | 否（簽名網址） | S3 相容端點 |
| `cashflow` | ChefCashFlow | 是 | `cashflow.railway.internal:8080` |
| `cashflow-db` | ChefCashFlow 的資料庫 | 否 | — |

說明：

- **服務名稱就是私有網路的主機名稱**，請照上表命名，合約檔裡的位址才會對得上。
- **每個系統各一個資料庫**。系統之間只走 API，分開最乾淨，也避免有人偷讀別人的表。
- API 服務固定監聽 `PORT=8080`。【DevOps 確認】私有網路使用 IPv6 位址，應用程式請監聽 `::`（例：`uvicorn main:app --host :: --port 8080`），否則可能只收得到公開網址的流量；細節以 Railway「Private Networking → How it works」文件為準。
- `/integration/*` 端點**只在私有網路上開放**。公開網址的反向代理或應用程式路由要擋掉 `/integration/` 開頭的路徑（見 2.2）。
- staging 與 production 的私有網路彼此隔離，staging 的 ledger 只會連到 staging 的 cashflow。

### 2.2 擋掉公開網址的 `/integration/*`

Railway 的公開網域與私有網路打到同一個服務，所以要在應用程式裡判斷：

```python
# FastAPI 範例（守門層 middleware）
PRIVATE_SUFFIX = ".railway.internal"

@app.middleware("http")
async def integration_only_private(request, call_next):
    if request.url.path.startswith(("/integration/", "/api/v1/integration/")):
        host = request.headers.get("host", "")
        if not host.split(":")[0].endswith(PRIVATE_SUFFIX) and settings.INTEGRATION_MODE == "private":
            return JSONResponse({"code": "FORBIDDEN", "message": "integration 端點只接受私有網路"}, status_code=403)
    return await call_next(request)
```

### 2.3 如果 ChefCashFlow 無法搬進同一個 project

Railway 文件只說明同一 project environment 內的私有網路，沒有說明跨 project。若 ChefCashFlow 必須留在別的 project：

- ② ③ ④ 改走 ledger 的公開網址，⑤ 改走 cashflow 的公開網址。
- 雙方 `INTEGRATION_MODE=public`，每個請求除了金鑰，**還要加 HMAC 簽章**（見 3.4）。
- 其他合約內容完全不變。

【待確認】ChefCashFlow 目前部署在哪個 Railway project。

---

## 3. 身分驗證：系統金鑰

### 3.1 金鑰清單

每一條「呼叫方 → 提供方」一把，不共用：

| 金鑰名稱 | 呼叫方存放的變數 | 提供方 | 允許的端點 |
| --- | --- | --- | --- |
| `sk_cashflow_ledger_…` | cashflow：`LEDGER_API_KEY` | ledger | `GET open-items`、`GET balances`、`POST settlements`、`POST bank-only-items` |
| `sk_ledger_invoice_…` | ledger：`INVOICE_API_KEY` | invoice | `GET invoice-events`、`GET pending-count`、`GET invoices/{id}/image` |
| `sk_ledger_cashflow_…` | ledger：`CASHFLOW_API_KEY` | cashflow | `GET companies`、`GET stores`、`GET bank-accounts` |
| `sk_invoice_cashflow_…` | invoice：`CASHFLOW_API_KEY` | cashflow | `GET companies`、`GET stores` |

### 3.2 產生金鑰

由 Bill 或 DevOps 在自己的電腦產生，不要用線上產生器：

```bash
python3 -c "import secrets; print('sk_cashflow_ledger_' + secrets.token_urlsafe(32))"
python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "<剛產生的金鑰>"
```

### 3.3 存放

- **呼叫方**：金鑰原文放在自己服務的 Railway 變數（例：cashflow 的 `LEDGER_API_KEY`）。
- **提供方**：只存 SHA-256 雜湊，放在 Railway 變數 `INTEGRATION_CLIENTS`（JSON），啟動時載入；或寫入守門層的 `guard.service_clients` 表。

```json
[
  {"client": "cashflow", "key_sha256": ["9f2c…（現用）", "a71d…（輪替中的新金鑰，可為空）"],
   "allow": ["GET /integration/v1/open-items", "GET /integration/v1/balances",
             "POST /integration/v1/settlements", "POST /integration/v1/bank-only-items"]}
]
```

- 驗證方式：取 `Authorization: Bearer` 後的字串算 SHA-256，以固定時間比較（`hmac.compare_digest`）比對清單。
- 每次呼叫記錄 `client`、端點、結果、`request_id` 到稽核紀錄；**不記錄金鑰本身**。

### 3.4 公開網址模式的 HMAC 簽章（僅 2.3 情況需要）

每把金鑰另配一把簽章密鑰 `whsec_…`。與金鑰不同，簽章密鑰雙方都要存原文：呼叫方用來簽、提供方用來驗，各自放在 Railway 變數 `INTEGRATION_SIGNING_SECRET`。

```text
X-Timestamp: 1790395200                       ← Unix 秒
X-Signature: v1=<hex(HMAC_SHA256(簽章密鑰, 簽章字串))>

簽章字串 = X-Timestamp + "\n" + HTTP 方法 + "\n" + 路徑含查詢字串 + "\n" + hex(SHA256(請求內容))
           （GET 的請求內容為空字串）
```

提供方檢查：時間差在 300 秒內、簽章相符、同一簽章 300 秒內沒出現過。

### 3.5 輪替

每年一次，或任何人員異動、疑似外洩時立即輪替：

1. 產生新金鑰，把**新雜湊加進**提供方的 `INTEGRATION_CLIENTS`（新舊並存）並重新部署。
2. 更新呼叫方的變數為新金鑰並重新部署。
3. 確認稽核紀錄裡已全部使用新金鑰後，從提供方**移除舊雜湊**。

---

## 4. 排程（Railway Cron）

Railway Cron 規則：時間一律 **UTC**；兩次執行至少間隔 5 分鐘；可能延遲幾分鐘；上一次還在跑時，這一次會跳過。工作做完必須結束程序並關閉資料庫連線。

| 服務 | 工作 | 啟動指令 | Cron（UTC） | 台北時間 |
| --- | --- | --- | --- | --- |
| `invoice-worker` | 掃收件匣、擷取、歸檔 | `python -m worker scan-inboxes` | `*/15 * * * *` | 每 15 分鐘（:00、:15、:30、:45） |
| `ledger-worker` | 同步發票事件 | `python -m worker sync-invoices` | `5-59/15 * * * *` | 每 15 分鐘（:05、:20、:35、:50），錯開發票系統 |
| `ledger-worker` | 每月固定費用、折舊、獎金預提 | `python -m worker run-recurring` | `5 0 1 * *` | 每月 1 日 08:05 |
| `ledger-worker` | 主檔快取更新 | `python -m worker refresh-directory` | `40 * * * *` | 每小時 :40 |
| `cashflow`（自訂） | 讀 open-items → 核對 → 回寫 | 依 ChefCashFlow 現況 | 建議銀行簿子匯入後觸發，另每天一次 | — |

同一個 worker 服務只能設一組 Cron，若要多個排程，可拆成多個 Railway 服務（同一份程式碼、不同啟動指令），或用一個每 5 分鐘的排程在程式內判斷該跑哪些工作。【DevOps 決定】

---

## 5. 重試與錯誤處理

| 情況 | 呼叫方怎麼做 | 提供方怎麼做 |
| --- | --- | --- |
| 網路錯誤、逾時（10 秒）、`503` | 指數退避重試：1、2、4、8 分鐘，最多 5 次；POST 重試**必須沿用同一個 Idempotency-Key** | 回 `Retry-After` |
| `401` / `403` | 不重試，寫錯誤日誌並通知（金鑰設定錯） | 記錄稽核 |
| `409 IDEMPOTENCY_CONFLICT` | 不重試；代表程式產生 key 的邏輯有錯 | — |
| `200` 但有 `rejected` | 逐筆記錄原因；`DOCUMENT_NOT_FOUND`、`DOCUMENT_VOID` 代表本地快取過期，重新讀 open-items | — |
| `410 CURSOR_EXPIRED`（發票事件） | ledger 不帶 cursor 重新完整同步；以來源唯一鍵去重，不會重複入帳 | 保留事件至少 180 天 |
| 提供方停機一整天 | 下一輪排程照常嘗試；ledger 總覽顯示「暫時讀不到 XX」 | 恢復後不需特別處理 |

逾時建議：讀取 10 秒、寫入 30 秒。

---

## 6. 環境變數範本

### 6.1 ledger（帳務損益系統）

```bash
APP_ENV=production
PORT=8080
INTEGRATION_MODE=private                       # private / public（public 需 HMAC）
DATABASE_URL=${{ledger-db.DATABASE_URL}}       # Railway 參照變數
INVOICE_BASE_URL=http://invoice.railway.internal:8080
INVOICE_API_KEY=                               # sk_ledger_invoice_…
CASHFLOW_BASE_URL=http://cashflow.railway.internal:8080
CASHFLOW_API_KEY=                              # sk_ledger_cashflow_…
INTEGRATION_CLIENTS=                           # JSON：允許呼叫 ledger 的金鑰雜湊（見 3.3）
BUCKET=${{ledger-files.BUCKET}}
ACCESS_KEY_ID=${{ledger-files.ACCESS_KEY_ID}}
SECRET_ACCESS_KEY=${{ledger-files.SECRET_ACCESS_KEY}}
ENDPOINT=${{ledger-files.ENDPOINT}}
REGION=${{ledger-files.REGION}}
GOOGLE_OAUTH_CLIENT_ID=                        # 使用者登入
GOOGLE_OAUTH_CLIENT_SECRET=
ALLOWED_EMAIL_DOMAIN=chefteng.com
ANTHROPIC_API_KEY=
```

（帳務規則類變數見《帳務損益系統 開發文件》第 12 節。）

### 6.2 invoice（發票系統）新增

```bash
PORT=8080
INTEGRATION_MODE=private
INTEGRATION_CLIENTS=                           # 允許 ledger 呼叫的金鑰雜湊
CASHFLOW_BASE_URL=http://cashflow.railway.internal:8080
CASHFLOW_API_KEY=                              # sk_invoice_cashflow_…
INVOICE_EVENT_RETENTION_DAYS=180
```

### 6.3 cashflow（ChefCashFlow）新增

```bash
LEDGER_BASE_URL=http://ledger.railway.internal:8080
LEDGER_API_KEY=                                # sk_cashflow_ledger_…
INTEGRATION_CLIENTS=                           # 允許 ledger、invoice 讀主檔的金鑰雜湊
LEDGER_SYNC_MODE=incremental                   # incremental / full
```

---

## 7. 各系統要做的事

### 7.1 ChefCashFlow

- [ ] 新增門市主檔（`stores`），提供 `GET /integration/v1/stores`
- [ ] 提供 `GET /integration/v1/companies`、`/bank-accounts`；清理重複登錄的銀行帳戶並設定 `merged_into`
- [ ] 每筆銀行交易有永久不變的 `cash_txn_id`（重匯同一份銀行簿子不能產生新 ID）
- [ ] 應付表匯入功能改為讀 `open-items`（原 M9 勾稽的資料來源）
- [ ] 核對完成後呼叫 `settlements`；對不到的收支呼叫 `bank-only-items`
- [ ] 資金預警改用 `open-items` 的 `outstanding`、`due_date` 與 `balances`
- [ ] 不再計算損益（原 M10 移到 ledger）
- [ ] 監聽 `::`、服務命名為 `cashflow`、實作 `GET /integration/v1/health`

### 7.2 發票系統

- [ ] `invoice.stores` 新增 `directory_store_id`
- [ ] 新增 `invoice_events` 表（附錄 B），在發票狀態變更的**同一個交易**內寫入事件
- [ ] 提供 `invoice-events`、`pending-count`、`invoices/{id}/image`、`health`
- [ ] 公司與門市改從 cashflow 的 `/companies`、`/stores` 讀取

### 7.3 帳務損益系統

- [ ] 提供 `open-items`、`settlements`、`bank-only-items`、`balances`、`health`
- [ ] `integration_requests` 表記錄 Idempotency-Key 與第一次的回應（保留 30 天）
- [ ] `sync-invoices` 排程：讀事件、建立應付、成功後才前進游標

---

## 8. 對接測試順序

依序完成，每一步通過才往下：

| 步驟 | 測試內容 | 通過條件 |
| --- | --- | --- |
| 1 | 三個系統的 `health` 互打 | 私有網路互通；公開網址打 `/integration/*` 回 403 |
| 2 | 金鑰：正確、錯誤、權限外的端點 | 200 / 401 / 403 |
| 3 | ⑤ 主檔：ledger 讀 companies、stores | 6 個公司實體、門市清單正確 |
| 4 | ② open-items：完整同步、增量同步 | 筆數與 ledger 畫面一致；結清後出現在 `removed` |
| 5 | ③ settlements：exact、fee_diff、partial、amount_only、reverse | 狀態依 4.2 規則變化；amount_only 進待確認 |
| 6 | ③ 冪等：同一請求送兩次；同 key 不同內容 | 第二次 `duplicates` 計數、不重複套用；不同內容回 409 |
| 7 | ③ bank-only-items：利息、還本、集團內調撥 | 進 ledger 待確認，建議類別正確 |
| 8 | ① invoice-events：確認、修改、作廢、重開 | ledger 應付對應建立、更新、作廢、轉草稿 |
| 9 | 停機測試：關掉 invoice 一小時再開 | ledger 自動補齊，無重複 |
| 10 | 端到端（下例） | 全流程 30 分鐘內完成，Bill 在應付畫面看到已付 |

**端到端範例**：文衡店把協昌果菜行的發票上傳到收件資料夾 → 發票系統確認並歸檔、寫入 `confirmed` 事件 → ledger 在下一輪讀到事件，建立應付、到期日 9/30、8 月蔬果成本增加 → cashflow 讀 open-items 看到這筆 → 匯入 9/28 的銀行簿子，比對到上海 3578 付給協昌的轉帳 → 呼叫 settlements（`exact`）→ ledger 應付畫面顯示「已付・9/28 上海3578」。

---

## 附錄 A：`suggested_kind` 與建議類別

| suggested_kind | 意思 | 建議類別 | 進損益 |
| --- | --- | --- | --- |
| `bank_fee` | 銀行手續費、匯費 | 營運費用【類別代碼待定】 | 是 |
| `interest` | 借款利息 | N01 利息支出 | 是 |
| `loan_principal` | 還本 | B02 借款本金 | 否 |
| `loan_drawdown` | 借款撥入 | B02 借款本金（負數） | 否 |
| `payroll` | 薪轉 | 對應薪資單據的結清，不建新單 | — |
| `insurance` | 勞健保扣款 | 對應勞健保應付或預估入帳 | — |
| `deposit` | 押金付出或退回 | B03 押金 | 否 |
| `shareholder` | 股東往來 | B04 股東往來 | 否 |
| `intercompany_fund` | 集團內公司之間調撥 | B05 集團內資金往來 | 否 |
| `business_tax` | 營業稅繳納 | B06 營業稅 | 否 |
| `refund` | 退款 | 依原單據 | — |
| `other` | 無法判斷 | B99 其他（月結前必須改歸） | 否 |

## 附錄 B：發票系統 `invoice_events` 表

```sql
CREATE TABLE invoice.invoice_events (
  sequence     BIGSERIAL PRIMARY KEY,                 -- 只增不減，即游標
  event_id     UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  event_type   TEXT NOT NULL CHECK (event_type IN ('confirmed','updated','voided','reopened')),
  invoice_id   UUID NOT NULL REFERENCES invoice.invoices(id),
  company_id   TEXT NOT NULL,
  snapshot     JSONB NOT NULL,                        -- 事件當下的完整發票（InvoiceSnapshot）
  actor        TEXT,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX invoice_events_company ON invoice.invoice_events (company_id, sequence);
```

讀取時只回傳 `occurred_at < now() - interval '5 seconds'` 的事件，避免讀到同時進行中、尚未提交的較小序號（BIGSERIAL 在併發交易下可能後提交先取號）。

## 附錄 C：帳務損益系統 `integration_requests` 表（冪等）

```sql
CREATE TABLE ledger.integration_requests (
  idempotency_key TEXT NOT NULL,
  client          TEXT NOT NULL,                      -- cashflow
  endpoint        TEXT NOT NULL,
  body_sha256     TEXT NOT NULL,                      -- 判斷「同 key 不同內容」
  response_status INT NOT NULL,
  response_body   JSONB NOT NULL,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (client, idempotency_key)
);
```

## 附錄 D：參考資料

- Railway：Private Networking　https://docs.railway.com/networking/private-networking
- Railway：Cron Jobs　https://docs.railway.com/reference/cron-jobs
- Railway：Storage Buckets　https://docs.railway.com/storage-buckets
