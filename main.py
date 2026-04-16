"""
main.py
監測港交所披露易「申請版本」頁面。
使用 Playwright 抓取 JS 動態渲染頁面。
只處理「最新發佈日期為今天（香港時間）」的新申請人。
records.json 用於防止同一天內重複發送通知。
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
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

SOURCE_URL   = "https://www1.hkexnews.hk/app/appindex.html?lang=zh"
RECORDS_FILE = "records.json"
PRIMARY_MODEL  = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
HK_TZ        = pytz.timezone("Asia/Hong_Kong")


# ── Gemini ─────────────────────────────────────────────────────────────────────
def model_request(prompt_text: str, api_key: str, output_limit: int = 1024) -> str:
    client = genai.Client(api_key=api_key)
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
    try:
        requests.post(
            api_url,
            json={"chat_id": chat_id, "text": message_text},
            timeout=30,
        ).raise_for_status()
        logger.info("Telegram 訊息已發送。")
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


# ── 日期判斷 ───────────────────────────────────────────────────────────────────
def is_today_hk(date_str: str) -> bool:
    date_str = date_str.strip()
    today_hk = datetime.now(HK_TZ).date()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).date() == today_hk
        except ValueError:
            continue
    logger.warning("無法解析日期格式：%s", date_str)
    return False


# ── 港交所抓取（Playwright）────────────────────────────────────────────────────
def fetch_todays_applicants() -> list[dict]:
    """
    使用 Playwright 渲染港交所申請版本頁面（JS 動態頁面）。
    只回傳「最新發佈日期 = 今天（香港時間）」的申請人。

    HTML 結構（確認）：
      tbody[aria-live="polite"] > tr.record-ap-phip
        td.col-posting-date
          span.mobile-list-body      → "15/04/2026"（日期）
        td.col-applicants
          div.mobile-list-body
            div.applicant-name       → 公司名稱
    """
    from playwright.sync_api import sync_playwright

    today_hk = datetime.now(HK_TZ).date()
    logger.info("香港時間今天：%s", today_hk.strftime("%d/%m/%Y"))
    applicants: list[dict] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-HK",
            )
            page = context.new_page()

            logger.info("Playwright 開啟港交所頁面…")
            page.goto(SOURCE_URL, timeout=60000, wait_until="networkidle")

            # 等待表格主體渲染完成
            try:
                page.wait_for_selector(
                    "tbody[aria-live='polite'] tr.record-ap-phip",
                    timeout=20000,
                )
                logger.info("表格已渲染完成。")
            except Exception:
                logger.warning("等待表格逾時，嘗試直接解析。")

            page.wait_for_timeout(2000)
            html_content = page.content()
            browser.close()

        soup = BeautifulSoup(html_content, "lxml")

        tbody = soup.find("tbody", attrs={"aria-live": "polite"})
        if not tbody:
            logger.warning("找不到 tbody[aria-live='polite']。")
            # 印出部分 HTML 協助診斷
            logger.info("頁面片段（前5000字）:\n%s", html_content[:5000])
            return applicants

        rows = tbody.find_all("tr", class_="record-ap-phip")
        logger.info("頁面共找到 %d 行記錄。", len(rows))

        for row in rows:
            # 取日期
            date_td   = row.find("td", class_="col-posting-date")
            date_span = date_td.find("span", class_="mobile-list-body") if date_td else None
            date_text = date_span.get_text(strip=True) if date_span else ""

            if not date_text or not is_today_hk(date_text):
                continue

            # 取申請人名稱
            name_td   = row.find("td", class_="col-applicants")
            if not name_td:
                continue

            name_div = name_td.find("div", class_="applicant-name")
            if name_div:
                name_text = name_div.get_text(strip=True)
            else:
                body_div  = name_td.find("div", class_="mobile-list-body")
                name_text = body_div.get_text(strip=True) if body_div else ""

            name_text = re.sub(r"\s+", " ", name_text).strip()

            if name_text and len(name_text) > 1:
                applicants.append({"name": name_text, "date": date_text})
                logger.info("今日申請人：%s（%s）", name_text, date_text)

        logger.info("今天（%s）符合條件申請人：%d 個。",
                    today_hk.strftime("%d/%m/%Y"), len(applicants))

    except Exception as exc:
        logger.error("Playwright 執行失敗: %s", exc)

    return applicants


# ── 報告生成 ───────────────────────────────────────────────────────────────────
def extract_report_body(raw: str, company_name: str, fallback: str) -> str:
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    start_idx = None
    for i, line in enumerate(lines):
        if company_name[:4] in line or "成立於" in line or "成立于" in line:
            start_idx = i
            break
    if start_idx is not None:
        body_lines = []
        for line in lines[start_idx:]:
            body_lines.append(line)
            if "呈批" in line:
                break
        if len(body_lines) >= 2:
            if not any("呈批" in l for l in body_lines):
                body_lines.append("呈批。")
            return "\n".join(body_lines)
    if "呈批" in raw:
        return raw[:raw.index("呈批") + 3].strip()
    return fallback


def generate_report(company_name: str, api_key: str) -> str:
    prompt = f"""你是一位專業財經分析師。請使用 Google Search 搜尋以下公司的背景資料：

公司名稱：{company_name}

請查找以下資訊：
1. 成立年份
2. 主要業務（簡短描述，不超過 30 字）
3. 最新財務年月（例如 2024/12）
4. 最新年度營業額（請標明幣種，例如 CNY 9.2 億）

若找不到某項確切數字，請填「招股書待披露」，絕對不可自行估計或捏造數據。
只輸出以下三行，不要加任何額外說明或前言，最後一行必須是「呈批。」：

「{company_name}」成立於[成立年份]年，該司主營[業務內容]。
該司已在香港聯交所主板上市遞交A1 form，[最新財務年月]營業額為[最新營業額]，為境外IPO目標戶，具業務拓展潛力，擬拓展該戶境外IPO業務。
呈批。"""

    fallback = (
        f"「{company_name}」成立於招股書待披露年，該司主營招股書待披露。\n"
        f"該司已在香港聯交所主板上市遞交A1 form，招股書待披露營業額為招股書待披露，"
        f"為境外IPO目標戶，具業務拓展潛力，擬拓展該戶境外IPO業務。\n"
        f"呈批。"
    )

    raw = model_request(prompt, api_key)
    if not raw:
        return fallback
    return extract_report_body(raw, company_name, fallback)


# ── 主程式 ─────────────────────────────────────────────────────────────────────
def main():
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    bot_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not all([gemini_key, bot_token, chat_id]):
        logger.error("缺少必要的環境變數。即將退出。")
        return

    notified_today = load_records(RECORDS_FILE)
    today_hk_str   = datetime.now(HK_TZ).strftime("%d/%m/%Y")
    logger.info("今天（%s）已通知記錄：%d 個。", today_hk_str, len(notified_today))

    wait_sec = random.randint(120, 300)
    logger.info("等待 %d 秒後開始抓取…", wait_sec)
    time.sleep(wait_sec)

    todays_applicants = fetch_todays_applicants()

    if not todays_applicants:
        logger.info("今天目前沒有新申請人，本次執行結束。")
        return

    new_to_notify = [
        a for a in todays_applicants
        if a["name"] not in notified_today
    ]
    logger.info("尚未通知的今日申請人：%d 個。", len(new_to_notify))

    if not new_to_notify:
        logger.info("今日所有申請人均已發送通知，無需重複發送。")
        return

    newly_notified = []
    for applicant in new_to_notify:
        name = applicant["name"]
        logger.info("處理：%s", name)
        report = generate_report(name, gemini_key)
        send_telegram(report, bot_token, chat_id)
        newly_notified.append(name)
        time.sleep(3)

    updated = list(dict.fromkeys(notified_today + newly_notified))
    save_records(RECORDS_FILE, updated)
    push_to_github(
        RECORDS_FILE,
        f"chore: 更新今日通知記錄 {today_hk_str} [{len(newly_notified)} 筆]",
    )


if __name__ == "__main__":
    main()