"""發票系統「廠商應付彙總表」匯入：一檔多公司、依統編分公司、個人遮蔽、重複匯入不重複入帳。"""
from tests.helpers import xlsx

HEAD = ["入帳公司名稱", "公司統編", "門市名稱", "門市代碼", "發票月份", "入帳月份", "廠商名稱", "統一編號", "發票張數",
        "含稅應付總計", "預計付款日"]


def _rows(extra_amount=0):
    return [HEAD,
            ["博仁餐飲", "24671416", "裕誠店", "博仁餐飲/裕誠店", "2026-09", "2026-09", "協昌菓菜行", "13647452", 1, 21000,
             "2026-09-30 前後"],
            ["名勝文衡分公司", "60737850", "文衡門市", "名勝文衡分公司/文衡門市", "2026-09", "2026-09", "明台產物保險股份有限公司",
             "79264504", 2, 10500 + extra_amount, "2026-09-30 前後"],
            ["博竑食品分公司", "93549405", "大樂店", "博竑食品分公司/大樂店", "2026-0", "2026-09", "張鈞", "T123144011", 1,
             10000, "2026-09-30 前後"],
            ["博竑食品", "42799631", "博竑食品", "博竑食品/總部", "2026-10", "2026-10", "協昌菓菜行", "13647452", 1, 5250,
             "2026-10-31 前後"]]


def _upload(client, rows, kind="invoice_summary"):
    r = client.post("/api/v1/imports", data={"kind": kind, "period": "2026-09"},
                    files={"file": ("summary.xlsx", xlsx(rows), "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


def test_summary_import_routes_rows_to_companies(client):
    b = _upload(client, _rows(), kind="ap_sheet")            # 選錯成應付表也會自動辨識
    assert b["kind"] == "invoice_summary" and b["company_id"] == "*"
    assert b["errors"] == [] and b["row_count"] == 4 and b["total_amount"] == 46750
    codes = {w["code"] for w in b["warnings"]}
    assert {"ID_NUMBER", "BAD_INVOICE_MONTH", "OTHER_PERIOD"} <= codes
    assert "T123144011" not in str(b)                          # 身分證字號不能出現在任何回應裡
    r = client.post(f"/api/v1/imports/{b['id']}/commit")
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 4

    docs = client.get("/api/v1/documents", params={"source_kind": "invoice_summary", "page_size": 50}).json()["items"]
    by = {(d["company_id"], d["counterparty"]["name"]): d for d in docs}
    assert by[("comp-borren", "協昌菓菜行")]["store_ids"] == ["store-br-yucheng"]
    assert by[("comp-1790322196308", "明台產物保險股份有限公司")]["tax_amount"] == 500     # 10500 含稅 → 10000 未稅
    ind = by[("comp-1790263075210", "張鈞")]
    assert ind["has_invoice"] is False and ind["tax_amount"] == 0                       # 個人：不還原稅額
    assert by[("comp-1", "協昌菓菜行")]["period_keys"] == ["2026-10"]                   # 依入帳月份記帳


def test_summary_reimport_skips_and_replaces(client):
    b1 = _upload(client, _rows())
    client.post(f"/api/v1/imports/{b1['id']}/commit")
    b2 = _upload(client, _rows(extra_amount=2100))           # 同一份再匯一次，只有保險金額變了
    actions = sorted(p["action"] for p in b2["preview"])
    assert actions == ["create", "skip", "skip", "skip"] or actions == ["replace", "skip", "skip", "skip"]
    res = client.post(f"/api/v1/imports/{b2['id']}/commit").json()
    assert res["skipped"] == 3 and res["replaced"] == 1 and res["created"] == 1
    docs = client.get("/api/v1/documents", params={"source_kind": "invoice_summary", "page_size": 50}).json()["items"]
    assert len(docs) == 4                                       # 沒有重複入帳
    ins = [d for d in docs if d["counterparty"]["name"] == "明台產物保險股份有限公司"]
    assert len(ins) == 1 and ins[0]["settle_amount"] == 12600


def test_import_survives_file_store_failure(client, monkeypatch):
    """正式環境的檔案儲存寫不進去時（例如容器目錄沒有權限），匯入仍要能完成。"""
    import app.feature.imports.service as svc

    class Broken:
        def put(self, *a, **k):
            raise PermissionError("[Errno 13] Permission denied: './.files'")

    monkeypatch.setattr(svc, "get_file_store", lambda: Broken())
    b = _upload(client, _rows())
    assert b["status"] == "parsed" and b["row_count"] == 4
    assert b["warnings"][0]["code"] == "FILE_NOT_STORED"
    assert client.post(f"/api/v1/imports/{b['id']}/commit").json()["created"] == 4
