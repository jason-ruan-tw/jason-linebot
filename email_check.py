#!/usr/bin/env python3
"""
大成 EIP 信箱監控 — cookie 快取版
平常只用 requests + cookie 打 API，cookie 失效才跑 Playwright 重新登入。
"""

import json
import os
import xml.etree.ElementTree as ET
import requests
import urllib3
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings()

EIP_LOGIN_URL = "https://eip.dachan.com/Login?ReturnUrl=/EHome"
EIP_USER      = "10013262"
EIP_PASS      = "Jason2002911225"
MAIL_SSO_URL  = "https://eip.dachan.com/SSOSubSysLoginWin/e81be206-d1d3-4f48-ad6a-debafb523e58"
INBOX_API     = (
    "https://mail.dachan.com/mail/10013262.nsf/iNotes/Proxy/"
    "?OpenDocument&Form=s_ReadViewEntries"
    "&PresetFields=FolderName;(%24Inbox),UnreadOnly;0,s_UsingHttps;1,hc;$98,noPI;1"
    "&TZType=UTC&Start=1&Count=30&resortdescending=5"
)

LINE_TOKEN   = "GQ7j41XU5eTF46OZBBsfqra/AF6tIec2aGkmKswrx/ymyCyTlbmhoqOl2H0cDo7gBQm8IkDf6Zib4tQ6OXBGQuqotzk4IyphDJubGs0Kc+23hbxmu/HknMVNVWRd1c1Y2PD1ryGBN6BHzYVPZtF1VgdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "U7818f4e68740285a54aff722d7c05863"

TAIPEI     = timezone(timedelta(hours=8))
COOKIE_FILE = "/tmp/eip_cookies.json"
STATE_FILE  = "/tmp/eip_seen.json"


# ── LINE 推播 ─────────────────────────────────────
def send_line(message: str):
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Email] LINE 推播失敗: {e}")
        return False


# ── Cookie 管理 ───────────────────────────────────
def _load_cookies() -> dict:
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cookies(cookies: dict):
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f)


def _make_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.verify = False
    for name, val in cookies.items():
        s.cookies.set(name, val)
    return s


# ── Playwright 登入，回傳 cookies dict ────────────
def _playwright_login() -> dict:
    print("[Email] Cookie 失效，啟動 Playwright 重新登入...")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(EIP_LOGIN_URL)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.fill("#UserID", EIP_USER)
        page.fill("#UserPwd", EIP_PASS)
        page.click("#LoginSubmit")
        page.wait_for_load_state("networkidle", timeout=15000)

        page.goto(MAIL_SSO_URL)
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(3000)

        raw = page.context.cookies()
        browser.close()

    cookies = {c["name"]: c["value"] for c in raw}
    _save_cookies(cookies)
    print(f"[Email] 登入成功，取得 {len(cookies)} 個 cookie")
    return cookies


# ── 取得有效 Session ──────────────────────────────
def _get_session() -> requests.Session:
    cookies = _load_cookies()
    if cookies:
        s = _make_session(cookies)
        try:
            r = s.get(INBOX_API, timeout=10)
            if r.status_code == 200 and "<viewentry" in r.text:
                return s  # cookie 還有效
        except Exception:
            pass
    # cookie 失效 → 重新登入
    cookies = _playwright_login()
    return _make_session(cookies)


# ── 解析收件匣 XML ────────────────────────────────
def _parse_domino_datetime(dt_str: str) -> str:
    try:
        dt_str = dt_str.replace(",", ".").rstrip("Z")
        dt = datetime.strptime(dt_str[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI).strftime("%m/%d %H:%M")
    except Exception:
        return dt_str


def _parse_inbox(xml_text: str) -> list:
    emails = []
    try:
        root = ET.fromstring(xml_text)
        for entry in root.findall("viewentry"):
            unid = entry.get("unid", "")
            sender = subject = date_str = ""
            for col in entry.findall("entrydata"):
                num = col.get("columnnumber")
                if num == "2":
                    t = col.find("text"); sender = t.text if t is not None else ""
                elif num == "4":
                    t = col.find("text"); subject = t.text if t is not None else ""
                elif num == "5":
                    t = col.find("datetime"); date_str = t.text if t is not None else ""
            if unid and sender and subject:
                emails.append({"unid": unid, "sender": sender,
                                "subject": subject, "date": _parse_domino_datetime(date_str)})
    except Exception as e:
        print(f"[Email] XML 解析失敗: {e}")
    return emails


# ── 已讀狀態 ──────────────────────────────────────
def _load_seen() -> set:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen(seen: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)


# ── 主要入口（供外部呼叫）────────────────────────
def check_and_notify():
    """檢查新信件並推播到 LINE，適合每 5 分鐘排程呼叫"""
    try:
        session = _get_session()
        r = session.get(INBOX_API, timeout=10)
        emails = _parse_inbox(r.text)

        seen = _load_seen()
        new_emails = [e for e in emails if e["unid"] not in seen]

        for email in reversed(new_emails):
            msg = (
                f"📬 新信件通知\n"
                f"寄件人：{email['sender']}\n"
                f"主旨：{email['subject']}\n"
                f"時間：{email['date']}"
            )
            ok = send_line(msg)
            seen.add(email["unid"])
            print(f"[Email] {'✅' if ok else '❌'} {email['sender']}｜{email['subject']}")

        if not new_emails:
            now = datetime.now(TAIPEI).strftime("%H:%M")
            print(f"[Email] {now} 沒有新信件（共 {len(emails)} 封）")

        _save_seen(seen)

    except Exception as e:
        print(f"[Email] 檢查失敗: {e}")
