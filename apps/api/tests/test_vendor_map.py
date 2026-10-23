"""廠商類別對應表：設定後發票彙總表直接入帳；先匯入後設定，草稿自動入帳、待確認結案。"""
from tests.helpers import xlsx
from tests.test_invoice_summary import _rows, _upload


def _map(client, rows):
    r = client.post("/api/v1/settings/vendor-map/import",
                    files={"file": ("map.xlsx", xlsx(rows), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_mapping_first_then_import_posts_directly(client):
    res = _map(client, [["統一編號", "廠商名稱", "類別代碼"],
                        ["13647452", "協昌菓菜行", "C01"],
                        ["79264504", "明台產物保險股份有限公司", "O04"],
                        ["T1*****011", "張鈞", "E08"]])
    assert res["created"] == 3 and res["errors"] == []
    b = _upload(client, _rows())
    assert all(p["status"] == "posted" for p in b["preview"])
    client.post(f"/api/v1/imports/{b['id']}/commit")
    st = client.get("/api/v1/pl/statement", params={"period_from": "2026-09", "period_to": "2026-09",
                                                    "columns": "month"}).json()
    lines = {l["category_code"]: l["values"]["2026-09"] for sec in st["sections"] for l in sec["lines"]}
    assert lines["C01"] == 20000 and lines["O04"] == 10000 and lines["E08"] == 10000
    assert client.get("/api/v1/review-items", params={"kind": "unclassified"}).json()["items"] == []


def test_mapping_after_import_posts_waiting_drafts(client):
    b = _upload(client, _rows())
    client.post(f"/api/v1/imports/{b['id']}/commit")
    open_items = client.get("/api/v1/review-items", params={"kind": "unclassified"}).json()["items"]
    assert len(open_items) == 4
    vm = {v["name"]: v for v in client.get("/api/v1/settings/vendor-map").json()["items"]}
    r = client.patch(f"/api/v1/settings/vendor-map/{vm['協昌菓菜行']['id']}", json={"category_code": "C01"})
    assert r.json()["applied_drafts"] == 2                     # 兩家公司各一張
    res = _map(client, [["統一編號", "廠商名稱", "類別代碼"], ["79264504", "明台產物保險", "O04"]])
    assert res["updated"] == 1 and res["applied_drafts"] == 1
    left = client.get("/api/v1/review-items", params={"kind": "unclassified"}).json()["items"]
    assert len(left) == 1                                       # 只剩張鈞
    vm = {v["tax_id"]: v for v in client.get("/api/v1/settings/vendor-map").json()["items"] if v["tax_id"]}
    assert vm["79264504"]["name"] == "明台產物保險"                 # 以對應表的名稱為準
    assert "明台產物保險股份有限公司" in vm["79264504"]["aliases"]


def test_mapping_rejects_income_and_unknown_codes(client):
    res = _map(client, [["統一編號", "廠商名稱", "類別代碼"], ["13647452", "協昌菓菜行", "R01"],
                        ["79264504", "明台", "Z99"], ["12345678", "空白類別", ""]])
    assert len(res["errors"]) == 2 and res["skipped_blank"] == 1 and res["created"] == 0


def test_export_roundtrip(client):
    _map(client, [["統一編號", "廠商名稱", "類別代碼"], ["13647452", "協昌菓菜行", "C01"]])
    r = client.get("/api/v1/settings/vendor-map/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    res = client.post("/api/v1/settings/vendor-map/import",
                      files={"file": ("back.xlsx", r.content, "application/octet-stream")}).json()
    assert res["unchanged"] == 1 and res["errors"] == []


def test_freight_category_available(client):
    cats = {c["code"]: c for c in client.get("/api/v1/settings/categories").json()["items"]}
    assert cats["E13"]["name"] == "運費物流" and cats["E13"]["pl_section"] == "opex"
    assert [cats[c]["name"] for c in ("E14", "E15", "E16")] == ["交通費", "設備費", "系統租用費"]
    res = _map(client, [["統一編號", "廠商名稱", "類別代碼"], ["06898848", "新竹物流股份有限公司", "E13"]])
    assert res["created"] == 1 and res["errors"] == []
