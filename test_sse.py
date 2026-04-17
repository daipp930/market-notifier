import requests
import xml.etree.ElementTree as ET

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*",
}

url = "https://www.sse.com.cn/home/rss/announcement.xml"
print("測試上交所 RSS Feed：" + url)

try:
    resp = requests.get(url, headers=headers, timeout=30)
    print("HTTP 狀態碼：" + str(resp.status_code))
    print("原始回應前 800 字：")
    print(resp.text[:800])

    if resp.status_code == 200:
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        print("\n共找到 " + str(len(items)) + " 個條目。")
        for i, item in enumerate(items[:5]):
            title = item.find("title")
            desc  = item.find("description")
            link  = item.find("link")
            print(f"\n── 第 {i+1} 筆 ──")
            print("title:", title.text if title is not None else "N/A")
            print("desc: ", desc.text[:200] if desc is not None and desc.text else "N/A")
            print("link: ", link.text if link is not None else "N/A")
except Exception as e:
    print("失敗：" + str(e))
