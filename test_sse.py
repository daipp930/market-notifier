"""
臨時測試腳本：不做任何篩選，直接印出上交所 API 回傳的原始結構。
確認 API 可連線、欄位名稱正確後即可刪除此檔案。
"""
import time
import json
import requests

headers = {
    "Referer":    "https://www.sse.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 測試端點一：queryCompanyBulletin.do（原本腳本使用的）
url1 = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
params1 = {
    "isPagination":       "true",
    "keyWord":            "发行H股",
    "pageHelp.pageSize":  25,
    "pageHelp.pageNo":    1,
    "pageHelp.beginPage": 1,
    "pageHelp.endPage":   5,
}

# 測試端點二：infodisclosure/queryTmp.do（不加日期限制）
url2 = "https://query.sse.com.cn/infodisclosure/queryTmp.do"
params2 = {
    "isPagination":       "true",
    "keyWord":            "发行H股",
    "pageHelp.pageSize":  25,
    "pageHelp.pageNo":    1,
    "pageHelp.beginPage": 1,
    "pageHelp.endPage":   5,
    "_":                  int(time.time() * 1000),
}

for label, url, params in [("端點一", url1, params1), ("端點二", url2, params2)]:
    print(f"\n{'='*50}")
    print(f"測試 {label}: {url}")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        print(f"HTTP 狀態碼: {resp.status_code}")
        print(f"原始回應前 500 字:\n{resp.text[:500]}")
        try:
            data = resp.json()
            print(f"JSON 型態: {type(data)}")
            if isinstance(data, list):
                print(f"直接是陣列，共 {len(data)} 筆")
                if data:
                    print(f"第一筆的欄位名稱: {list(data[0].keys())}")
                    print(f"第一筆內容:\n{json.dumps(data[0], ensure_ascii=False, indent=2)}")
            elif isinstance(data, dict):
                print(f"字典的頂層 Keys: {list(data.keys())}")
                # 嘗試找資料陣列
                for key in ["data", "result", "pageHelp"]:
                    if key in data:
                        val = data[key]
                        if isinstance(val, list):
                            print(f"data['{key}'] 是陣列，共 {len(val)} 筆")
                            if val:
                                print(f"第一筆欄位: {list(val[0].keys())}")
                                print(f"第一筆:\n{json.dumps(val[0], ensure_ascii=False, indent=2)}")
                        elif isinstance(val, dict):
                            print(f"data['{key}'] 是字典，Keys: {list(val.keys())}")
                            inner = val.get("data", [])
                            if isinstance(inner, list):
                                print(f"data['{key}']['data'] 共 {len(inner)} 筆")
                                if inner:
                                    print(f"第一筆欄位: {list(inner[0].keys())}")
                                    print(f"第一筆:\n{json.dumps(inner[0], ensure_ascii=False, indent=2)}")
        except Exception as e:
            print(f"JSON 解析失敗: {e}")
    except Exception as e:
        print(f"請求失敗: {e}")
