"""應付／應收清單上方的合計：全部符合條件的單據（不受分頁），選總公司含分公司，可依門市篩。"""
from tests.helpers import payable


def _sum(client, **params):
    r = client.get("/api/v1/documents/summary", params={"doc_type": "payable", "period": "2026-08", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_summary_counts_everything_and_filters(client):
    payable(client, company="comp-1", store="store-bh-judan", amount=10500, vendor="甲", invoice_no="AA00000001")
    payable(client, company="comp-1790263075210", store="store-bh-dale", amount=21000, vendor="乙", invoice_no="AA00000002")
    payable(client, company="comp-1790263075210", store="store-bh-dale", amount=5250, vendor="丙", invoice_no="AA00000003",
            method="petty_cash")
    payable(client, company="comp-1", store=None, amount=3150, vendor="丁", invoice_no="AA00000004")
    payable(client, company="comp-borren", store="store-br-yucheng", amount=1050, vendor="戊", invoice_no="AA00000005")

    allg = _sum(client)
    assert allg["count"] == 5 and allg["total"] == 40950 and allg["settled"] == 5250 and allg["outstanding"] == 35700

    bh = _sum(client, company_id="comp-1")                      # 總公司含兩家分公司
    assert bh["count"] == 4 and bh["total"] == 39900
    kh = _sum(client, company_id="comp-1790263075210")          # 只看分公司
    assert kh["count"] == 2 and kh["total"] == 26250
    assert _sum(client, store_id="store-bh-dale")["total"] == 26250              # 不選公司也能選門市
    assert _sum(client, company_id="comp-1", store_id="unallocated")["total"] == 3150   # 公司層級
    assert _sum(client, settle_status="open,partial")["count"] == 4

    lst = client.get("/api/v1/documents", params={"doc_type": "payable", "period": "2026-08", "page_size": 2}).json()
    assert lst["total"] == 5 and len(lst["items"]) == 2                          # 清單分頁，但合計是全部
    p3 = client.get("/api/v1/documents", params={"doc_type": "payable", "period": "2026-08", "page_size": 2,
                                                 "page": 3}).json()
    assert len(p3["items"]) == 1
