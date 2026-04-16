"""
sse_monitor.py
監測上海證券交易所「发行H股」相關公告。
直接呼叫上交所後端 GET JSON API，無需解析 HTML。
比對「證券代碼」是否已存在於 sse_records.json，若未見過則視為新增。
sse_records.json 最多保留 1000 筆記錄。
"""

import os
import json
import re
import time
import random
import logging
import subprocess
from datetime import datetime

import pytz
import requests
from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

RECORDS_FILE    = "sse_records.json"
PRIMARY_MODEL   = "gemini-2.5-flash"
FALLBACK_MODEL  = "gemini-2.5-flash-lite"
MESSAGE_LIMIT   = 3500
EXCHANGE_NAME   = "上交所"
HK_TZ           = pytz.timezone("Asia/Hong_Kong")
MAX_RECORDS     = 1000

# 上交所 GET JSON API（你親測有效）
SSE_API_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"

# ── Gemini ─────────────────────────────────────────────────────────────────────
def model_request(prompt_text: str, api_key: str, output_limit: int = 1024) -> str:
    client   = genai.Client(api_key=api_key)
    tool_cfg = types.Tool(google_search=types.GoogleSearch())

    for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
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
                    if model != PRIMARY_MODEL:
                        logger.info("已使用備用模型 %s 成功生成。", model)
                    return text
                logger.warning("[%s] Gemini 傳回空字串 (嘗試 %d)", model, attempt + 1)
            except Exception as exc:
                exc_str = str(exc)
                logger.error("[%s] 請求失敗 (嘗試 %d): %s", model, attempt + 1, exc_str)
                if attempt == 0:
                    if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                        wait_match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s",
                                               exc_str, re.I)
                        wait_sec = int(float(wait_match.group(1))) + 3 if wait_match else 30
                        logger.warning("[%s] 配額超限，等待 %d 秒後重試…", model, wait_sec)
                        time.sleep(wait_sec)
                    else:
                        time.sleep(6)
        logger.warning("[%s] 兩次嘗試均失敗，切換備用模型…", model)

    return ""

# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(message_text: str, bot_token: str, chat_id: str) -> None:
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks  = [message_text[i:i + MESSAGE_LIMIT]
               for i in range(0, len(message_text), MESSAGE_LIMIT)]
    for chunk in chunks:
        try:
            requests.post(
                api_url,
                json={"chat_id": chat_id, "text": chunk},
                timeout=30,
            ).raise_for_status()
            time.sleep(1)
        except Exception as exc:
            logger.error("Telegram 發送失敗: %s", exc)

# ── GitHub push ────────────────────────────────────────────────────────────────
def push_to_github(filepath: str, commit_msg: str) -> None:
    def _run(cmd):
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    for attempt in range(2):
        try:
            _run(["git", "config", "--global", "user.email",
                  "github-actions[bot]@users.noreply.github.com"])
            _run(["git", "config", "--global", "user.name", "github-actions[bot]"])
            _run(["git", "add", filepath])
            diff = subprocess.run(
                ["git", "diff", "--staged", "--exit-code"],
                capture_output=True,
            )
            if diff.returncode == 0:
                logger.info("沒有變更需要提交。")
                return
            _run(["git", "commit", "-m", commit_msg])
            _run(["git", "push"])
            logger.info("已推送 %s 到 GitHub。", filepath)
            return
        except subprocess.CalledProcessError as exc:
            logger.error("Git push 失敗 (嘗試 %d): %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(10)
                try:
                    subprocess.run(["git", "pull", "--rebase"],
                                   check=True, capture_output=True)
                except Exception:
                    pass

# ── 記錄檔讀寫 ─────────────────────────────────────────────────────────────────
def load_records(path: str) -> list:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.error("讀取 %s 失敗: %s", path, exc)
    return []

def save_records(path: str, data: list) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

# ── 上交所抓取（GET JSON API，你親測有效版本）────────────────────────────────
def fetch_sse_announcements() -> list[dict]:
    """
    直接呼叫上交所後端 GET JSON API。
    端點：https://query.sse.com.cn/security/stock/queryCompanyBulletin.do
    必要 Headers：Referer（突破防爬蟲）、User-Agent
    回傳 JSON 結構：data["pageHelp"]["data"] 陣列
    提取欄位：SECURITY_CODE（證券代碼）、SECURITY_ABBR（證券簡稱）、TITLE（公告標題）
    """
    headers = {
        "Referer":    "https://www.sse.com.cn/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    params = {
        "isPagination":       "true",
        "keyWord":            "发行H股",
        "pageHelp.pageSize":  25,
        "pageHelp.pageNo":    1,
        "pageHelp.beginPage": 1,
        "pageHelp.endPage":   5,
    }

    announcements: list[dict] = []
    seen_codes:    set[str]   = set()

    for attempt in range(3):
        try:
            logger.info("上交所 API 請求（嘗試 %d）…", attempt + 1)
            resp = requests.get(
                SSE_API_URL,
                params=params,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()

            raw_text = resp.text.strip()
            if not raw_text or raw_text == "null":
                logger.info("上交所 API 回傳空結果。")
                return announcements

            data = resp.json()

            # 資料位於 data["pageHelp"]["data"]
            rows: list = []
            if (isinstance(data, dict)
                    and "pageHelp" in data
                    and isinstance(data["pageHelp"], dict)
                    and "data" in data["pageHelp"]
                    and isinstance(data["pageHelp"]["data"], list)):
                rows = data["pageHelp"]["data"]
            elif isinstance(data, list):
                rows = data

            logger.info("上交所 API 回傳 %d 筆。", len(rows))

            for row in rows:
                if not isinstance(row, dict):
                    continue

                sec_code = (
                    row.get("SECURITY_CODE") or row.get("secCode") or ""
                ).strip()
                sec_name = (
                    row.get("SECURITY_ABBR") or row.get("secName") or ""
                ).strip()
                title = (
                    row.get("TITLE") or row.get("title") or ""
                ).strip()

                if not sec_code or not title:
                    continue

                if sec_code in seen_codes:
                    continue
                seen_codes.add(sec_code)

                announcements.append({
                    "code":     sec_code,
                    "name":     sec_name,
                    "title":    title,
                    "exchange": EXCHANGE_NAME,
                })
                logger.info("符合條件：[%s] %s — %s", sec_code, sec_name, title)

            return announcements  # 成功即返回

        except requests.exceptions.JSONDecodeError as exc:
            logger.warning("JSON 解析失敗 (嘗試 %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)
        except Exception as exc:
            logger.error("API 請求失敗 (嘗試 %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)

    logger.error("上交所 API 三次嘗試均失敗。")
    return announcements

# ── 報告生成 ───────────────────────────────────────────────────────────────────
def extract_report_body(raw: str, company_name: str, fallback: str) -> str:
    lines     = [l.strip() for l in raw.splitlines() if l.strip()]
    start_idx = None
    for i, line in enumerate(lines):
        if company_name[:4] in line or "成立於" in line or "成立于" in line:
            start_idx = i
            break
    if start_idx is not None:
        body_lines: list[str] = []
        for line in lines[start_idx:]:
            body_lines.append(line)
            if "供參考" in line or "供参考" in line:
                break
        if len(body_lines) >= 2:
            if not any("供參考" in l or "供参考" in l for l in body_lines):
                body_lines.append("供參考。")
            return "\n".join(body_lines)
    if "供參考" in raw or "供参考" in raw:
        idx = max(raw.find("供參考"), raw.find("供参考"))
        return raw[:idx + 4].strip()
    return fallback

def generate_report(company_name: str, stock_code: str, api_key: str) -> str:
    prompt = f"""你是一位專業分析員。請使用 Google Search 搜尋以下 A 股公司的公開資料。

公司名稱：{company_name}
A 股代碼：{stock_code}
上市交易所：{EXCHANGE_NAME}

請查找：
1. 公司成立年份
2. 主要業務（不超過 30 字）
3. 最新財務年月（例如 2024/12）
4. 最新年度營業額（含幣種，例如 CNY 9.2 億）

若找不到確切數字，填「資料待查」，絕對不可捏造數據。
只輸出以下三行，不要加任何額外說明或前言，最後一行必須是「供參考。」：

【A股早期預警】「{company_name}（{stock_code}）」成立於[成立年份]年，該司主營[業務內容]。
該司已在A股{EXCHANGE_NAME}公告籌劃發行H股，擬於香港聯交所上市，[最新財務年月]營業額為[最新營業額]，為境外IPO高潛力目標戶，建議優先跟進。
供參考。"""

    fallback = (
        f"【A股早期預警】「{company_name}（{stock_code}）」成立於資料待查年，該司主營資料待查。\n"
        f"該司已在A股{EXCHANGE_NAME}公告籌劃發行H股，擬於香港聯交所上市，"
        f"資料待查營業額為資料待查，為境外IPO高潛力目標戶，建議優先跟進。\n"
        f"供參考。"
    )

    raw = model_request(prompt, api_key)
    if not raw:
        return fallback

    # 防截斷：若結尾不含「供參考」，觸發重試一次
    if "供參考" not in raw and "供参考" not in raw:
        logger.warning("回應未含結尾標記「供參考」，重試一次…")
        raw = model_request(prompt, api_key)
        if not raw:
            return fallback

    return extract_report_body(raw, company_name, fallback)

# ── 主程式 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    bot_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([gemini_key, bot_token, chat_id]):
        logger.error("缺少必要的環境變數（GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）。即將退出。")
        return

    seen_codes   = load_records(RECORDS_FILE)
    today_hk_str = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    logger.info("目前 %s 已記錄證券代碼：%d 筆。", RECORDS_FILE, len(seen_codes))

    wait_sec = random.randint(120, 300)
    logger.info("等待 %d 秒後開始抓取…", wait_sec)
    time.sleep(wait_sec)

    announcements = fetch_sse_announcements()

    if not announcements:
        logger.info("上交所未抓取到任何符合條件公告，本次執行結束。")
        return

    new_items = [a for a in announcements if a["code"] not in seen_codes]
    logger.info("未曾通知的新代碼：%d 筆。", len(new_items))

    if not new_items:
        logger.info("所有公告代碼均已記錄，無需重複發送。")
        return

    newly_added: list[str] = []
    for item in new_items:
        logger.info("處理：[%s] %s — %s", item["code"], item["name"], item["title"])
        report = generate_report(
            company_name=item["name"] or item["code"],
            stock_code=item["code"],
            api_key=gemini_key,
        )
        send_telegram(report, bot_token, chat_id)
        newly_added.append(item["code"])
        time.sleep(3)

    # 合併並限制上限 1000 筆
    updated = list(dict.fromkeys(seen_codes + newly_added))
    if len(updated) > MAX_RECORDS:
        updated = updated[-MAX_RECORDS:]
        logger.info("記錄數超過上限，已裁剪至最新 %d 筆。", MAX_RECORDS)

    save_records(RECORDS_FILE, updated)
    push_to_github(
        RECORDS_FILE,
        f"chore: 更新上交所記錄 {today_hk_str} [{len(newly_added)} 筆新增]",
    )

if __name__ == "__main__":
    main()
