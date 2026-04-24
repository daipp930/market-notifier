"""
init_records.py
【首次運行專用 — 高效並發版】
以 ThreadPoolExecutor 並發抓取多個交易日，
大幅縮短初始化時間（約 80 個交易日，並發 5 個，預計 3-5 分鐘完成）。
"""

import json
import logging
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
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

RECORDS_FILE     = "cn_records.json"
START_DATE       = date(2026, 1, 1)
END_DATE         = date(2026, 4, 23)
MAX_WORKERS      = 5    # 同時並發請求數（不宜超過 5，避免觸發限速）
REQUEST_TIMEOUT  = 30   # 單次 API 請求最長等待秒數
RETRY_SLEEP      = 8    # 失敗後等待秒數

INCLUDE_KEYWORDS = [
    "发行H股", "發行H股",
    "H股发行", "H股發行",
    "香港联交所", "香港联合交易所",
]
EXCLUDE_KEYWORDS = [
    "回购", "回購", "注销", "註銷", "登记", "登記", "持股",
]

_rate_lock = threading.Semaphore(MAX_WORKERS)


def is_target(notice_type: str, title: str) -> bool:
    if notice_type.strip() != "其他":
        return False
    if not title.strip():
        return False
    if any(ex in title for ex in EXCLUDE_KEYWORDS):
        return False
    return any(inc in title for inc in INCLUDE_KEYWORDS)


def fetch_day(day_str: str) -> tuple[str, list]:
    with _rate_lock:
        for attempt in range(3):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(ak.stock_notice_report, symbol="全部", date=day_str)
                    try:
                        df = future.result(timeout=REQUEST_TIMEOUT)
                    except FutureTimeout:
                        logger.warning("⏱ %s 請求超時（第 %d 次）", day_str, attempt + 1)
                        if attempt < 2:
                            time.sleep(RETRY_SLEEP)
                        continue

                if df is None or df.empty:
                    return day_str, []

                results = []
                for _, row in df.iterrows():
                    notice_type = str(row.get("公告类型", ""))
                    title       = str(row.get("公告标题", ""))
                    code        = str(row.get("代码", "")).strip()
                    name        = str(row.get("名称", "")).strip()
                    if not is_target(notice_type, title):
                        continue
                    if not code or not code.startswith(("6", "0", "3")):
                        continue
                    results.append((code, name))
                return day_str, results

            except Exception as exc:
                logger.error("❌ %s 失敗（第 %d 次）：%s", day_str, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(RETRY_SLEEP)

    return day_str, []


def get_trading_days(start: date, end: date) -> list[str]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def main():
    if Path(RECORDS_FILE).exists():
        try:
            with open(RECORDS_FILE, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, dict) and len(existing) > 0:
                logger.warning(
                    "%s 已存在且有 %d 筆紀錄，跳過初始化。若需重新初始化請先清空該檔案。",
                    RECORDS_FILE, len(existing),
                )
                return
            else:
                logger.info("%s 存在但內容為空，繼續執行初始化。", RECORDS_FILE)
        except Exception as exc:
            logger.warning("讀取 %s 失敗（%s），繼續執行初始化。", RECORDS_FILE, exc)

    trading_days = get_trading_days(START_DATE, END_DATE)
    total = len(trading_days)
    logger.info("共 %d 個交易日，以 %d 個並發執行緒開始抓取…", total, MAX_WORKERS)

    records: dict[str, str] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_day, day): day for day in trading_days}

        for future in as_completed(futures):
            day_str, results = future.result()
            completed += 1
            for code, name in results:
                if code not in records:
                    records[code] = name
                    logger.info("[新增] %s %s", code, name)
            if completed % 10 == 0 or completed == total:
                logger.info(
                    "進度 %d/%d 個交易日，累計 %d 筆代碼。", completed, total, len(records)
                )

    ordered = dict(sorted(records.items()))
    with open(RECORDS_FILE, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, ensure_ascii=False, indent=2)

    logger.info(
        "✅ 初始化完成。共掃描 %d 個交易日，寫入 %d 筆不重複代碼至 %s。",
        total, len(records), RECORDS_FILE,
    )


if __name__ == "__main__":
    main()
