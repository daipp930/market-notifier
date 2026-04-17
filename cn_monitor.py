from pathlib import Path
import os, subprocess
os.makedirs('output', exist_ok=True)
content = r'''"""
cn_monitor.py
滬深兩市 H 股公告監測（首次初始化 + 日常增量監測）
- 首次運行：自動爬取 2026/01/01 ~ 2026/04/17，寫入 cn_records.json
- 日常運行：抓取當天公告，比對 cn_records.json；新股票代碼才觸發 Gemini + Telegram
- 條件：公告類型為「其他」，且標題含 H 股發行相關關鍵字
"""

import os
import json
import re
import time
import random
import logging
import subprocess
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytz
import requests
import akshare as ak
from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

RECORDS_FILE = "cn_records.json"
PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
MESSAGE_LIMIT = 3500
MAX_WORKERS = 2

SSE_EXCHANGE = "上交所"
SZSE_EXCHANGE = "深交所"
HK_TZ = pytz.timezone("Asia/Hong_Kong")

INCLUDE_KEYWORDS = [
    "发行H股", "發行H股",
    "H股发行", "H股發行",
    "香港联交所", "香港联合交易所",
]
EXCLUDE_KEYWORDS = [
    "回购", "回購", "注销", "註銷", "登记", "登記", "持股"
]


def load_records(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
                if isinstance(data, list):
                    return {str(code): "" for code in data}
        except Exception as exc:
            logger.error("讀取 %s 失敗：%s", path, exc)
    return {}


def save_records(path: str, data: dict) -> None:
    ordered = dict(sorted(data.items(), key=lambda kv: kv[0]))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, ensure_ascii=False, indent=2)


def push_to_github(filepaths: list, commit_msg: str) -> None:
    def run(cmd):
        return subprocess.run(cmd, check=True, capture_output=True, text=True)

    for attempt in range(2):
        try:
            run(["git", "config", "--global", "user.email", "github-actions@users.noreply.github.com"])
            run(["git", "config", "--global", "user.name", "github-actions[bot]"])
            for fp in filepaths:
                run(["git", "add", fp])
            diff = subprocess.run(["git", "diff", "--staged", "--exit-code"], capture_output=True)
            if diff.returncode == 0:
                logger.info("無檔案變更，略過 git push。")
                return
            run(["git", "commit", "-m", commit_msg])
            run(["git", "push"])
            logger.info("已 push 更新：%s", filepaths)
            return
        except subprocess.CalledProcessError as exc:
            logger.error("git push 失敗（第 %d 次）：%s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(10)
                try:
                    subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True)
                except Exception:
                    pass


def is_target_notice(row: dict) -> bool:
    notice_type = str(row.get("公告类型", "")).strip()
    title = str(row.get("公告标题", "")).strip()
    if notice_type != "其他":
        return False
    if not title:
        return False
    if any(ex in title for ex in EXCLUDE_KEYWORDS):
        return False
    return any(inc in title for inc in INCLUDE_KEYWORDS)


def fetch_day_notices(day_str: str) -> list:
    for attempt in range(3):
        try:
            df = ak.stock_notice_report(symbol="全部", date=day_str)
            if df is None or df.empty:
                return []
            notices = []
            seen_codes = set()
            for _, row in df.iterrows():
                if not is_target_notice(row):
                    continue
                code = str(row.get("代码", "")).strip()
                name = str(row.get("名称", "")).strip()
                title = str(row.get("公告标题", "")).strip()
                if not code or code in seen_codes:
                    continue
                if not code.startswith(("6", "0", "3")):
                    continue
                seen_codes.add(code)
                notices.append({
                    "code": code,
                    "name": name,
                    "title": title,
                    "exchange": SSE_EXCHANGE if code.startswith("6") else SZSE_EXCHANGE,
                })
            return notices
        except Exception as exc:
            logger.error("抓取 %s 失敗（第 %d 次）：%s", day_str, attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)
    return []


def do_initialization() -> None:
    logger.info("===== 偵測到首次運行，開始初始化歷史紀錄 =====")
    start_date = date(2026, 1, 1)
    end_date = date(2026, 4, 17)
    records = {}
    current = start_date

    while current <= end_date:
        if current.weekday() < 5:
            day_str = current.strftime("%Y%m%d")
            notices = fetch_day_notices(day_str)
            for notice in notices:
                records[notice["code"]] = notice["name"]
                logger.info("[Init] %s %s", notice["code"], notice["name"])
            time.sleep(1)
        current += timedelta(days=1)

    save_records(RECORDS_FILE, records)
    logger.info("初始化完成，共寫入 %d 筆代碼。", len(records))
    push_to_github([RECORDS_FILE], "chore: initialize cn records (2026-01-01 to 2026-04-17)")


def model_request(prompt_text: str, api_key: str, output_limit: int = 1024) -> str:
    client = genai.Client(api_key=api_key)
    tool_cfg = types.Tool(google_search=types.GoogleSearch())

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        gen_cfg = types.GenerateContentConfig(
            tools=[tool_cfg],
            temperature=0.1,
            max_output_tokens=output_limit,
        )
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt_text,
                    config=gen_cfg,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                logger.warning("Gemini %s 第 %d 次回傳空內容。", model, attempt + 1)
            except Exception as exc:
                exc_str = str(exc)
                logger.error("Gemini %s 第 %d 次失敗：%s", model, attempt + 1, exc_str)
                if attempt == 0:
                    if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                        wait_match = re.search(r"retry[\s\S]*?(\d+(?:\.\d+)?)\s*s", exc_str, re.I)
                        wait_sec = int(float(wait_match.group(1)) * 1.3) if wait_match else 30
                        time.sleep(wait_sec)
                    else:
                        time.sleep(6)
        logger.warning("模型 %s 失敗，切換下一個模型。", model)
    return ""


def extract_report_body(raw: str, company_name: str, fallback: str) -> str:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    start_idx = None
    for i, line in enumerate(lines):
        if company_name[:4] in line or "公司簡介" in line or "概況" in line:
            start_idx = i
            break

    if start_idx is not None:
        body_lines = []
        for line in lines[start_idx:]:
            body_lines.append(line)
            if "呈批。" in line:
                break
        if len(body_lines) >= 2:
            if not any("呈批。" in line for line in body_lines):
                body_lines.append("呈批。")
            return "\n".join(body_lines)

    if "呈批。" not in fallback:
        fallback += "\n呈批。"
    return fallback


def generate_report(company_name: str, stock_code: str, exchange: str, api_key: str) -> str:
    prompt = (
        f"請用 Google Search 搜尋 A 股上市公司「{company_name}」（股票代碼：{stock_code}，交易所：{exchange}）的資料，"
        f"並用繁體中文撰寫一份簡報，必須包含：\n"
        f"1. 公司主營業務及行業地位（2-3句）\n"
        f"2. 近期財務亮點（最近一個完整財年，包含營收及盈利概況）\n"
        f"3. 為何此時籌劃發行H股赴港上市的可能原因分析\n"
        f"4. 對香港資本市場的潛在影響\n\n"
        f"格式要求：每段以粗體標題開始，結尾必須是「呈批。」"
    )
    fallback = (
        f"【{company_name}（{stock_code}）· {exchange}】\n"
        f"正在籌劃發行H股並申請在香港聯合交易所上市。\n"
        f"（Gemini 未能生成詳細報告，請查閱相關公告。）\n呈批。"
    )

    for attempt in range(2):
        raw = model_request(prompt, api_key, 1024)
        if not raw:
            return fallback
        report = extract_report_body(raw, company_name, fallback)
        if report.rstrip().endswith("呈批。"):
            return report
        logger.warning("報告結尾防截斷檢查失敗（第 %d 次），重試。", attempt + 1)
        time.sleep(3)
    return fallback


def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = [text[i:i + MESSAGE_LIMIT] for i in range(0, len(text), MESSAGE_LIMIT)]
    for chunk in chunks:
        try:
            resp = requests.post(api_url, json={"chat_id": chat_id, "text": chunk}, timeout=60)
            resp.raise_for_status()
            time.sleep(1)
        except Exception as exc:
            logger.error("Telegram 發送失敗：%s", exc)


def process_notice(notice: dict, api_key: str, bot_token: str, chat_id: str) -> str:
    code = notice["code"]
    name = notice["name"] or code
    title = notice["title"]
    logger.info("開始處理：[%s] %s — %s", code, name, title)
    report = generate_report(name, code, notice["exchange"], api_key)
    send_telegram(report, bot_token, chat_id)
    return code


def do_daily_monitor(gemini_key: str, bot_token: str, chat_id: str) -> None:
    logger.info("===== 執行日常增量監測 =====")
    records = load_records(RECORDS_FILE)
    logger.info("載入現有紀錄 %d 筆。", len(records))

    today_str = datetime.now(HK_TZ).strftime("%Y%m%d")
    today_hk_str = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    notices = fetch_day_notices(today_str)
    new_notices = [notice for notice in notices if notice["code"] not in records]

    logger.info("今日符合條件公告 %d 筆，其中新代碼 %d 筆。", len(notices), len(new_notices))
    if not new_notices:
        logger.info("今日無新代碼，結束。")
        return

    new_records = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_notice, notice, gemini_key, bot_token, chat_id): notice
            for notice in new_notices
        }
        for future in as_completed(futures):
            notice = futures[future]
            try:
                code = future.result()
                new_records[code] = notice["name"]
            except Exception as exc:
                logger.error("處理公告失敗 [%s]：%s", notice["code"], exc)

    if new_records:
        records.update(new_records)
        save_records(RECORDS_FILE, records)
        push_to_github([RECORDS_FILE], f"chore({today_hk_str}): add {len(new_records)} new cn notices")
        logger.info("已更新 cn_records.json，共新增 %d 筆。", len(new_records))


def main() -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([gemini_key, bot_token, chat_id]):
        logger.error("缺少必要環境變數（GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）。")
        return

    if not os.path.exists(RECORDS_FILE):
        do_initialization()
        return

    wait_sec = random.randint(30, 90)
    logger.info("隨機等待 %d 秒後開始日常監測。", wait_sec)
    time.sleep(wait_sec)
    do_daily_monitor(gemini_key, bot_token, chat_id)


if __name__ == "__main__":
    main()
'''
Path('output/cn_monitor.py').write_text(content, encoding='utf-8')
res = subprocess.run(['python','-m','py_compile','output/cn_monitor.py'], capture_output=True, text=True)
print('returncode=', res.returncode)
print(res.stderr)
