# 銀行交易 Webhook 規格（ChefCashFlow → 帳務損益系統）

版本 1.0・2026-10-23

## 1. 這是做什麼的

會計在 ChefCashFlow 上傳銀行簿子之後，ChefCashFlow 把**銀行交易本身**（日期、金額、摘要、對方戶名與帳號）用一個 HTTPS 請求送給帳務損益系統。比對由帳務損益系統做：

- **支出**：先用對方帳號、再用戶名／摘要／備註認出廠商，然後在**同一法人**（總公司＋分公司）的未付應付裡，找金額剛好相符的組合。一家公司一次匯一筆給廠商時，這一筆會被拆回各門市的應付單，全部標成已付。
- **收入**：同一法人裡金額完全相同、且只有一張的應收，標成已收。
- **對不到的**：進帳務系統的「待確認」，能猜到的單據組合放在建議裡，會計按一下就確認。

ChefCashFlow **不需要**知道帳務系統有哪些單據，也不需要寫比對規則。兩個系統可以在不同的 Railway project，走公開網址，用金鑰＋簽章保護。

## 2. 什麼時候呼叫

每次匯入銀行簿子完成後，把這次匯入的交易送一次（支出、收入都送）。一次最多 1,000 筆，多的分批送。

重送是安全的：同一個 `cash_txn_id` 已經處理過的會回 `duplicate`，不會重複標已付。

## 3. 請求

```
POST {LEDGER_WEBHOOK_URL}
例：https://ledger-api.chefteng.food/integration/v1/webhooks/bank-transactions
```

| 標頭 | 說明 |
| --- | --- |
| `Authorization: Bearer {LEDGER_API_KEY}` | 系統金鑰（帳務系統只存它的 SHA-256） |
| `Idempotency-Key: {uuid}` | 每批一個新的 UUID；**重試同一批時用同一個** |
| `X-Timestamp: {unix 秒}` | 送出當下的時間，和帳務系統的時間差不能超過 5 分鐘 |
| `X-Signature: v1={hex}` | 簽章，見第 4 節 |
| `Content-Type: application/json` | |

內容：

```json
{
  "event": "bank_transactions.imported",
  "items": [
    {
      "cash_txn_id": "txn_20261001_3926_0007",
      "cash_account_id": "acc-3926",
      "txn_date": "2026-10-01",
      "direction": "out",
      "amount": 1950545,
      "bank_memo": "跨行轉帳",
      "bank_note": "9月貨款",
      "counterparty_name": "美食家食材通路股",
      "counterparty_account_no": "0040****12345"
    }
  ]
}
```

| 欄位 | 必填 | 說明 |
| --- | --- | --- |
| `cash_txn_id` | 是 | ChefCashFlow 裡這筆交易的唯一 ID，永遠不變（同一筆交易重新匯入也要同一個） |
| `cash_account_id` | 是 | ChefCashFlow 的銀行帳戶 ID（與公司主檔的 bank_accounts 相同），帳務系統用它判斷是哪一家公司 |
| `txn_date` | 是 | 交易日 `YYYY-MM-DD` |
| `direction` | 是 | `out` 支出、`in` 收入 |
| `amount` | 是 | 正整數（元），不含正負號 |
| `bank_memo` | | 銀行摘要（例：跨行轉帳、票交媒體） |
| `bank_note` | | 備註 |
| `counterparty_name` | | 對方戶名（被銀行截短也沒關係） |
| `counterparty_account_no` | | 對方帳號；可以遮蔽，但**至少保留末 5 碼**。帳務系統用名稱比對成功一次後會記住，下次直接用帳號認 |

## 4. 簽章

```
待簽字串 = X-Timestamp + "\n" + "POST" + "\n" + 路徑 + "\n" + hex(SHA256(原始 body bytes))
X-Signature = "v1=" + hex(HMAC_SHA256(LEDGER_SIGNING_SECRET, 待簽字串))
```

路徑是 `/integration/v1/webhooks/bank-transactions`（不含網域）。簽章要用**實際送出的那串 bytes** 計算，送出前不要再重新序列化。

Python：

```python
import hashlib, hmac, json, time, uuid, httpx

def send_bank_transactions(items: list[dict]) -> dict:
    path = "/integration/v1/webhooks/bank-transactions"
    body = json.dumps({"event": "bank_transactions.imported", "items": items}, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    msg = f"{ts}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(LEDGER_SIGNING_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    r = httpx.post(LEDGER_BASE_URL + path, content=body, timeout=60, headers={
        "Authorization": f"Bearer {LEDGER_API_KEY}", "Idempotency-Key": str(uuid.uuid4()),
        "X-Timestamp": ts, "X-Signature": f"v1={sig}", "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()
```

Node.js：

```js
import crypto from "node:crypto";

export async function sendBankTransactions(items) {
  const path = "/integration/v1/webhooks/bank-transactions";
  const body = JSON.stringify({ event: "bank_transactions.imported", items });
  const ts = Math.floor(Date.now() / 1000).toString();
  const bodyHash = crypto.createHash("sha256").update(body, "utf8").digest("hex");
  const sig = crypto.createHmac("sha256", process.env.LEDGER_SIGNING_SECRET)
    .update(`${ts}\nPOST\n${path}\n${bodyHash}`).digest("hex");
  const res = await fetch(process.env.LEDGER_BASE_URL + path, {
    method: "POST", body,
    headers: { Authorization: `Bearer ${process.env.LEDGER_API_KEY}`, "Idempotency-Key": crypto.randomUUID(),
               "X-Timestamp": ts, "X-Signature": `v1=${sig}`, "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`ledger webhook ${res.status}: ${await res.text()}`);
  return res.json();
}
```

## 5. 回應

```json
{
  "matched": 4, "review": 1, "duplicate": 0, "rejected": 0,
  "results": [
    { "cash_txn_id": "txn_…0007", "status": "matched", "matched_by": "name",
      "counterparty": "美食家食材通路股份有限公司", "documents": ["…", "…"] },
    { "cash_txn_id": "txn_…0008", "status": "review", "reason": "認不出廠商", "suggested_documents": [] },
    { "cash_txn_id": "txn_…0009", "status": "duplicate" },
    { "cash_txn_id": "txn_…0010", "status": "rejected", "message": "不認得銀行帳戶 acc-xxxx" }
  ]
}
```

| status | 意思 | ChefCashFlow 要做什麼 |
| --- | --- | --- |
| `matched` | 已在帳務系統標成已付／已收（`documents` 是被結清的單據） | 可以在自己這邊標「已對帳」 |
| `review` | 進帳務系統的待確認，由會計處理 | 不用重送 |
| `duplicate` | 這個 `cash_txn_id` 以前處理過 | 不用重送 |
| `rejected` | 資料有問題（多半是帳戶 ID 不在主檔） | 修正後用新的 Idempotency-Key 重送 |

`matched_by`：`account`（對方帳號）、`name`（戶名）、`memo`（摘要）、`note`（備註）、`amount`（收入，以金額對）。

HTTP 狀態：

| 狀態 | 處理方式 |
| --- | --- |
| 200 | 成功（回應帶 `Idempotent-Replayed: true` 表示是重試、結果和第一次相同） |
| 401 | 金鑰或簽章錯誤、時間差超過 5 分鐘 → 檢查設定，不要自動重試 |
| 403 | 這把金鑰沒有呼叫此端點的權限 |
| 409 | 同一個 Idempotency-Key 送了不同內容 → 換新的 key |
| 422 | 欄位格式錯誤（回應 `details` 列出哪一欄） |
| 5xx／逾時 | 用**同一個** Idempotency-Key 重試（建議 1、5、30 分鐘後各一次） |

## 6. 設定

先產生兩組亂碼（各一次，兩邊用同一組）：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # API 金鑰
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # 簽章密鑰
python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "<API 金鑰>"
```

**帳務損益系統（Railway 後端服務）**

```text
INTEGRATION_MODE=public
INTEGRATION_SIGNING_SECRET=<簽章密鑰>
INTEGRATION_CLIENTS=[{"client":"cashflow","key_sha256":["<API 金鑰的 SHA-256>"],"allow":["POST /integration/v1/webhooks/bank-transactions"]}]
FRONTEND_PROXY_KEY=<另一串亂碼>
```

後端要有公開網域（Settings → Networking → Generate Domain，或自訂網域如 `ledger-api.chefteng.food`）。
有了公開網域之後，`FRONTEND_PROXY_KEY` 必須設：它讓 `/api/*` 只接受帳務台前端轉進來的請求；`/integration/*` 另外靠金鑰＋簽章保護。
前端服務要加上同一串：`LEDGER_PROXY_KEY=<同一串亂碼>`（前端和後端在同一個 project，`LEDGER_API_URL` 維持私有網路位址即可）。

**ChefCashFlow**

```text
LEDGER_BASE_URL=https://<帳務後端公開網域>
LEDGER_API_KEY=<API 金鑰>
LEDGER_SIGNING_SECRET=<簽章密鑰>
```

## 7. 注意事項

- 銀行帳戶主檔：帳務系統用 `cash_account_id` 判斷公司，所以 ChefCashFlow 的每個帳戶都要在帳務系統的帳戶主檔裡（目前博仁餐飲、博竑漢神分公司還沒有帳戶）。
- 比對只用「到期日在交易日 20 天內」的未付單據，避免把很久以後才到期的單據提早結清。
- 金額必須剛好相符才自動結清；手續費另扣、少付或多付都會進待確認。
- 自動結清錯了：在 ChefCashFlow 取消配對後，呼叫既有的 `POST /integration/v1/settlements`（`action: "reverse"`）取消（`INTEGRATION_CLIENTS` 的 `allow` 要加上 `"POST /integration/v1/settlements"`，簽章方式相同），或請會計在帳務系統處理。
