test_sse
"""
test_sse.py
臨時診斷腳本：測試上交所各 API 端點的連線狀況與回傳結構。
確認後請刪除此檔案。
"""

import time
import json
import requests

headers = {
    "Referer":          "https://www.sse.com.cn/",
    "User-Agent":       (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

# ── 三個待測端點 ────────────────────────────────────────────────────────────────

endpoints = [
    {
        "label":  "端點一：queryCompanyBulletin.do（原本腳本）",
        "method": "GET",
        "url":    "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do",
        "params": {
            "isPagination":       "true",
            "keyWord":            "发行H股",
            "pageHelp.pageSize":  25,
            "pageHelp.pageNo":    1,
            "pageHelp.beginPage": 1,
            "pageHelp.endPage":   5,
        },
        "json": None,
    },
    {
        "label":  "端點二：searchResult.do（主站搜尋）",
        "method": "GET",
        "url":    "https://www.sse.com.cn/home/search/searchResult.do",
        "params": {
            "siteid":  "hscdb",
            "adminid": "hscdb_sseproduct",
            "q":       "发行H股",
            "rows":    30,
            "start":   0,
            "sort":    "relevant",
        },
        "json": None,
    },
    {
        "label":  "端點三：disclosure/listedinfo GET",
        "method": "GET",
        "url":    "https://query.sse.com.cn/commonSoaQuery.do",
        "params": {
            "sqlId":             "COMMON_SSE_CP_XXPL_GGSZ_L",
            "isPagination":      "true",
            "pageHelp.pageNo":   1,
            "pageHelp.pageSize": 25,
            "NOTICE_TITLE":      "发行H股",
        },
        "json": None,
    },
]

# ── 輔助函數：遞迴展示 JSON 結構 ───────────────────────────────────────────────

def inspect(data, depth=0, max_depth=4):
    indent = "  " * depth
    if depth > max_depth:
        print(f"{indent}（層級過深，截斷）")
        return

    if isinstance(data, dict):
        print(f"{indent}dict，Keys: {list(data.keys())}")
        for k, v in data.items():
            print(f"{indent}  [{k}]：", end="")
            if isinstance(v, list):
                print(f"list，共 {len(v)} 筆")
                if v and isinstance(v[0], dict):
                    print(f"{indent}    第一筆欄位：{list(v[0].keys())}")
                    print(f"{indent}    第一筆內容：")
                    print(json.dumps(v[0], ensure_ascii=False, indent=2)
                          .replace("\n", f"\n{indent}    "))
            elif isinstance(v, dict):
                print(f"dict")
                inspect(v, depth + 2)
            else:
                val_str = str(v)
                print(val_str[:120] + ("…" if len(val_str) > 120 else ""))
    elif isinstance(data, list):
        print(f"{indent}直接是 list，共 {len(data)} 筆")
        if data and isinstance(data[0], dict):
            print(f"{indent}  第一筆欄位：{list(data[0].keys())}")
            print(f"{indent}  第一筆內容：")
            print(json.dumps(data[0], ensure_ascii=False, indent=2))
    else:
        print(f"{indent}{type(data).__name__}: {str(data)[:200]}")


# ── 主測試迴圈 ─────────────────────────────────────────────────────────────────

for ep in endpoints:
    print("\n" + "=" * 60)
    print(f"測試 {ep['label']}")
    print(f"URL: {ep['url']}")
    try:
        if ep["method"] == "GET":
            resp = requests.get(
                ep["url"],
                params=ep["params"],
                headers=headers,
                timeout=30,
            )
        else:
            resp = requests.post(
                ep["url"],
                json=ep["json"],
                headers=headers,
                timeout=30,
            )

        print(f"HTTP 狀態碼: {resp.status_code}")
        print(f"原始回應前 600 字:\n{resp.text[:600]}")
        print()

        if resp.status_code != 200:
            print("非 200，跳過 JSON 解析。")
            continue

        try:
            data = resp.json()
            print("── JSON 結構分析 ──")
            inspect(data)
        except Exception as e:
            print(f"JSON 解析失敗: {e}")

    except Exception as e:
        print(f"請求失敗: {e}")

    time.sleep(2)

print("\n" + "=" * 60)
print("測試完成。")
