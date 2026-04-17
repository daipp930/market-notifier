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
    },
    {
        "label":  "端點三：commonSoaQuery.do",
        "method": "GET",
        "url":    "https://query.sse.com.cn/commonSoaQuery.do",
        "params": {
            "sqlId":             "COMMON_SSE_CP_XXPL_GGSZ_L",
            "isPagination":      "true",
            "pageHelp.pageNo":   1,
            "pageHelp.pageSize": 25,
            "NOTICE_TITLE":      "发行H股",
        },
    },
]


def inspect(data, depth=0, max_depth=3):
    indent = "  " * depth
    if depth > max_depth:
        print(indent + "（層級過深，截斷）")
        return
    if isinstance(data, dict):
        print(indent + "dict，Keys: " + str(list(data.keys())))
        for k, v in data.items():
            if isinstance(v, list):
                print(indent + "  [" + k + "]：list，共 " + str(len(v)) + " 筆")
                if v and isinstance(v[0], dict):
                    print(indent + "    第一筆欄位：" + str(list(v[0].keys())))
                    print(indent + "    第一筆內容：")
                    print(json.dumps(v[0], ensure_ascii=False, indent=2))
            elif isinstance(v, dict):
                print(indent + "  [" + k + "]：dict")
                inspect(v, depth + 2)
            else:
                val_str = str(v)
                print(indent + "  [" + k + "]：" + val_str[:120])
    elif isinstance(data, list):
        print(indent + "直接是 list，共 " + str(len(data)) + " 筆")
        if data and isinstance(data[0], dict):
            print(indent + "  第一筆欄位：" + str(list(data[0].keys())))
            print(indent + "  第一筆內容：")
            print(json.dumps(data[0], ensure_ascii=False, indent=2))
    else:
        print(indent + str(type(data).__name__) + ": " + str(data)[:200])


for ep in endpoints:
    print("")
    print("=" * 60)
    print("測試 " + ep["label"])
    print("URL: " + ep["url"])
    try:
        resp = requests.get(ep["url"], params=ep["params"], headers=headers, timeout=30)
        print("HTTP 狀態碼: " + str(resp.status_code))
        print("原始回應前 600 字:")
        print(resp.text[:600])
        print("")
        if resp.status_code != 200:
            print("非 200，跳過 JSON 解析。")
        else:
            try:
                data = resp.json()
                print("── JSON 結構分析 ──")
                inspect(data)
            except Exception as e:
                print("JSON 解析失敗: " + str(e))
    except Exception as e:
        print("請求失敗: " + str(e))
    time.sleep(2)

print("")
print("=" * 60)
print("測試完成。")
