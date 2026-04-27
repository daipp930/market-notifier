"""
cn_monitor.py
【每日自動監測專用】
搜尋當天 A 股公告，篩選「公告類型=其他」且標題含 H 股發行關鍵字，
比對 cn_records.json（由 init_records.py 建立），
若股票代碼屬新紀錄，則呼叫 Gemini 生成分析報告並推送至 Telegram，
最後將新代碼寫回 cn_records.json 並 push 至 GitHub。
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import random
from datetime import datetime
from pathlib import Path

import pytz
import requests
import akshare as ak
from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
sys.stdout.reconfigure(line_buffering=True)
logger = logging.getLogger(__name__)

RECORDS_FILE   = "cn_records.json"
PRIMARY_MODEL  = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
MESSAGE_LIMIT  = 3500
HK_TZ          = pytz.timezone("Asia/Hong_Kong")

INCLUDE_KEYWORDS = [
    "发行H股", "發行H股",
    "H股发行", "H股發行",
    "香港联交所", "香港联合交易所",
]
EXCLUDE_KEYWORDS = [
    "回购", "回購", "注销", "註銷", "登记", "登記", "持股",
]


# ── JSON 讀寫 ─────────────────────────────────────────────────────────────

def load_records() -> dict:
    if Path(RECORDS_FILE).exists():
        try:
            with open(RECORDS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            if isinstance(data, list):
                return {str(c): "" for c in data}
        except Exception as exc:
            logger.error("讀取 %s 失敗：%s", RECORDS_FILE, exc)
    return {}


def save_records(records: dict) -> None:
    ordered = dict(sorted(records.items()))
    with open(RECORDS_FILE, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, ensure_ascii=False, indent=2)


# ── GitHub push ───────────────────────────────────────────────────────────

def push_to_github(commit_msg: str) -> None:
    def run(cmd):
        return subprocess.run(cmd, check=True, capture_output=True, text=True)

    for attempt in range(2):
        try:
            run(["git", "config", "--global", "user.email",
                 "github-actions@users.noreply.github.com"])
            run(["git", "config", "--global", "user.name", "github-actions[bot]"])
            run(["git", "add", RECORDS_FILE])
            diff = subprocess.run(
                ["git", "diff", "--staged", "--exit-code"], capture_output=True
            )
            if diff.returncode == 0:
                logger.info("無檔案變更，略過 git push。")
                return
            run(["git", "commit", "-m", commit_msg])
            run(["git", "push"])
            logger.info("已 push：%s", commit_msg)
            return
        except subprocess.CalledProcessError as exc:
            logger.error("git push 失敗（第 %d 次）：%s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(10)
                try:
                    subprocess.run(
                        ["git", "pull", "--rebase"], check=True, capture_output=True
                    )
                except Exception:
                    pass


# ── 公告篩選 ──────────────────────────────────────────────────────────────

def is_target(notice_type: str, title: str) -> bool:
    if notice_type.strip() != "其他":
        return False
    if not title.strip():
        return False
    if any(ex in title for ex in EXCLUDE_KEYWORDS):
        return False
    return any(inc in title for inc in INCLUDE_KEYWORDS)


def fetch_today_notices() -> list:
    today_str = datetime.now(HK_TZ).strftime("%Y%m%d")
    logger.info("抓取 %s 公告…", today_str)
    for attempt in range(3):
        try:
            df = ak.stock_notice_report(symbol="全部", date=today_str)
            if df is None or df.empty:
                return []
            results = []
            seen = set()
            for _, row in df.iterrows():
                notice_type = str(row.get("公告类型", ""))
                title       = str(row.get("公告标题", "")).strip()
                code        = str(row.get("代码", "")).strip()
                name        = str(row.get("名称", "")).strip()
                if not is_target(notice_type, title):
                    continue
                if not code or not code.startswith(("6", "0", "3")):
                    continue
                if code in seen:
                    continue
                seen.add(code)
                exchange = "上交所" if code.startswith("6") else "深交所"
                results.append({
                    "code": code, "name": name,
                    "title": title, "exchange": exchange,
                })
                logger.info("符合條件：[%s] %s — %s", code, name, title)
            return results
        except Exception as exc:
            logger.error("抓取失敗（第 %d 次）：%s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)
    return []


# ── Gemini ────────────────────────────────────────────────────────────────

def model_request(prompt: str, api_key: str) -> str:
    client   = genai.Client(api_key=api_key)
    tool_cfg = types.Tool(google_search=types.GoogleSearch())
    gen_cfg  = types.GenerateContentConfig(
        tools=[tool_cfg], temperature=0.1, max_output_tokens=1024,
    )
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt, config=gen_cfg,
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
                        m = re.search(r"retry[\s\S]*?(\d+(?:\.\d+)?)\s*s", exc_str, re.I)
                        time.sleep(int(float(m.group(1)) * 1.3) if m else 30)
                    else:
                        time.sleep(6)
        logger.warning("模型 %s 全部失敗，切換下一個。", model)
    return ""

def generate_report(name: str, code: str, exchange: str, api_key: str) -> str:
    prompt = (
        f"用繁體中文，嚴格只輸出以下兩段，無標題、無符號、無分析：\n\n"
        f"「{name}」成立於XXXX年，該司主營[主營業務一句話]。\n\n"
        f"該司擬發行H股股票並在香港聯交所上市，[最新完整財年]全年營業額為CNY XX.XX億元，為境外IPO目標戶，具業務拓展潛力，擬拓展該戶境外IPO業務。\n\n"
        f"呈批。"
    )

    fallback = (
        f"「{name}」成立於XXXX年，該司主營[請補充主營業務]。\n\n"
        f"該司擬發行H股股票並在香港聯交所上市，[最新完整財年]全年營業額為CNY XX.XX億元，"
        f"為境外IPO目標戶，具業務拓展潛力，擬拓展該戶境外IPO業務。\n\n"
        f"呈批。"
    )

    for attempt in range(3):  # 增加到 3 次重試
        raw = model_request(prompt, api_key)
        if not raw:
            continue

        # 強化防截斷：檢查是否包含關鍵元素
        if "營業額" not in raw or "CNY" not in raw:
            logger.warning("第 %d 次：缺少營業額資訊，重試。", attempt + 1)
            time.sleep(3)
            continue

        # 取到「呈批。」為止
        if "呈批。" in raw:
            idx = raw.rfind("呈批。")
            report = raw[:idx + 3].strip()
            
            # 確保有兩段（用 \n\n 分隔）
            if report.count('\n\n') >= 1 and report.rstrip().endswith("呈批。"):
                return report

        logger.warning("第 %d 次格式不符，重試。", attempt + 1)
        time.sleep(3)

    return fallback



# ── Telegram ──────────────────────────────────────────────────────────────

def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    url    = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = [text[i:i + MESSAGE_LIMIT] for i in range(0, len(text), MESSAGE_LIMIT)]
    for chunk in chunks:
        try:
            r = requests.post(
                url, json={"chat_id": chat_id, "text": chunk}, timeout=30
            )
            r.raise_for_status()
            time.sleep(1)
        except Exception as exc:
            logger.error("Telegram 發送失敗：%s", exc)


# ── 主流程 ────────────────────────────────────────────────────────────────

def main() -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    bot_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([gemini_key, bot_token, chat_id]):
        logger.error(
            "缺少環境變數（GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）。"
        )
        return

    if not Path(RECORDS_FILE).exists():
        logger.error(
            "%s 不存在！請先執行 init_records.py 建立歷史紀錄。", RECORDS_FILE
        )
        return

    records = load_records()
    logger.info("載入現有紀錄 %d 筆。", len(records))

    wait = random.randint(30, 90)
    logger.info("隨機等待 %d 秒後開始監測。", wait)
    time.sleep(wait)

    notices     = fetch_today_notices()
    new_notices = [n for n in notices if n["code"] not in records]
    logger.info(
        "今日符合條件 %d 筆，其中新代碼 %d 筆。", len(notices), len(new_notices)
    )

    if not new_notices:
        logger.info("今日無新代碼，結束。")
        return

    today_hk  = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    new_count = 0
    for notice in new_notices:
        code, name, exchange = notice["code"], notice["name"], notice["exchange"]
        logger.info("處理：[%s] %s (%s)", code, name, exchange)
        report = generate_report(name, code, exchange, gemini_key)
        send_telegram(report, bot_token, chat_id)
        records[code] = name
        new_count += 1
        time.sleep(3)

    save_records(records)
    push_to_github(f"chore({today_hk}): add {new_count} new H-share notice(s)")
    logger.info("完成，共新增 %d 筆代碼。", new_count)


if __name__ == "__main__":
    main()
