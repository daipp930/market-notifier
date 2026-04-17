import akshare as ak
from datetime import date

today = date.today().strftime("%Y%m%d")
print("測試日期：" + today)

try:
    df = ak.stock_notice_report(symbol="全部", date=today)
    print("回傳筆數：" + str(len(df)))
    print("欄位名稱：" + str(list(df.columns)))
    print("前 5 筆：")
    print(df.head(5).to_string())
    
    # 測試分流
    sse  = df[df["代码"].str.startswith("6")]
    szse = df[df["代码"].str.startswith(("0", "3"))]
    print("\n上交所公告筆數：" + str(len(sse)))
    print("深交所公告筆數：" + str(len(szse)))
except Exception as e:
    print("失敗：" + str(e))
    import traceback
    traceback.print_exc()
