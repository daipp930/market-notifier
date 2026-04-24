"""
init_records.py
【首次運行專用】
搜尋 2026/01/01 至 2026/04/23 的 A 股公告，
篩選「公告類型=其他」且標題含 H 股發行關鍵字，
將不重複的（股票代碼 -> 公司名稱）寫入 cn_records.json。
"""

import json
import logging
import sys
import time
import concurrent.futures  # ← 加在這裡
from datetime import date, timedelta
from pathlib import Path

import akshare as ak


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
sys.stdout.reconfigure(line_buffering=True)
logger = logging.getLogger(__name__)

RECORDS_FILE = "cn_records.json"
START_DATE = date(2026, 1, 1)
END_DATE   = date(2026, 4, 23)

INCLUDE_KEYWORDS = [
    "发行H股", "發行H股",
    "H股发行", "H股發行",
    "香港联交所", "香港联合交易所",
]
EXCLUDE_KEYWORDS = [
    "回购", "回購", "注销", "註銷", "登记", "登記", "持股",
]


def is_target(notice_type: str, title: str) -> bool:
    if notice_type.strip() != "其他":
        return False
    if not title.strip():
        return False
    if any(ex in title for ex in EXCLUDE_KEYWORDS):
        return False
    return any(inc in title for inc in INCLUDE_KEYWORDS)


# ↓ 刪掉原本這整個 function，換成下面的版本

def fetch_day(day_str: str) -> list:
    for attempt in range(3):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    ak.stock_notice_report, symbol="全部", date=day_str
                )
                try:
                    df = future.result(timeout=30)  # 最多等 30 秒
                except concurrent.futures.TimeoutError:
                    logger.warning("抓取 %s 超時（第 %d 次），跳過", day_str, attempt + 1)
                    if attempt < 2:
                        time.sleep(5)
                    continue

            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                notice_type = str(row.get("公告类型", ""))
                title = str(row.get("公告标题", ""))
                code = str(row.get("代码", "")).strip()
                name = str(row.get("名称", "")).strip()
                if not is_target(notice_type, title):
                    continue
                if not code or not code.startswith(("6", "0", "3")):
                    continue
                results.append((code, name))
            return results

        except Exception as exc:
            logger.error("抓取 %s 失敗（第 %d 次）：%s", day_str, attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)
    return []



def main():
    if Path(RECORDS_FILE).exists():
        try:
            with open(RECORDS_FILE, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, dict) and len(existing) > 0:
                logger.warning(
                    "%s 已存在且有 %d 筆紀錄，跳過初始化。若需重新初始化請先清空該檔案。",
                    RECORDS_FILE, len(existing)
                )
                return
            else:
                logger.info("%s 存在但內容為空，繼續執行初始化。", RECORDS_FILE)
        except Exception as exc:
            logger.warning("讀取 %s 失敗（%s），繼續執行初始化。", RECORDS_FILE, exc)

    records: dict[str, str] = {}
    current = START_DATE
    total_days = 0

    while current <= END_DATE:
        if current.weekday() < 5:  # 只處理工作日
            day_str = current.strftime("%Y%m%d")
            results = fetch_day(day_str)
            for code, name in results:
                if code not in records:
                    records[code] = name
                    logger.info("[新增] %s %s", code, name)
            total_days += 1
            if total_days % 10 == 0:
                logger.info("已掃描 %d 個交易日，累計 %d 筆代碼…", total_days, len(records))
            time.sleep(1)
        current += timedelta(days=1)

    ordered = dict(sorted(records.items()))
    with open(RECORDS_FILE, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, ensure_ascii=False, indent=2)

    logger.info("初始化完成。共掃描 %d 個交易日，寫入 %d 筆不重複代碼至 %s。",
                total_days, len(records), RECORDS_FILE)


if __name__ == "__main__":
    main()
