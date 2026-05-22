#!/usr/bin/env python3
"""
每日自動推播：盤前/盤後大盤 + 雷老闆/李永年影片重點
"""

import os
import re
import json
import subprocess
import requests
import urllib3
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings()

LINE_TOKEN   = "GQ7j41XU5eTF46OZBBsfqra/AF6tIec2aGkmKswrx/ymyCyTlbmhoqOl2H0cDo7gBQm8IkDf6Zib4tQ6OXBGQuqotzk4IyphDJubGs0Kc+23hbxmu/HknMVNVWRd1c1Y2PD1ryGBN6BHzYVPZtF1VgdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "U7818f4e68740285a54aff722d7c05863"
TAIPEI       = timezone(timedelta(hours=8))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

CHANNELS = {
    "雷老闆": "UCFsyPpT525Fass_s7fA2qhg",
    "李永年": "UCya5E2GyEep6HuEsmLUcyWA",
}

SCORE_MAP = {
    "買進": 4, "賣出": 4, "建議": 3, "目標": 4, "停損": 4, "停利": 3,
    "布局": 3, "佈局": 3, "主力": 3, "外資": 3, "法人": 3, "投信": 2,
    "漲停": 3, "跌停": 3, "強勢": 2, "弱勢": 2, "突破": 3, "支撐": 3,
    "壓力": 2, "回檔": 2, "反彈": 2, "多頭": 2, "空頭": 2,
    "台積電": 2, "聯電": 2, "鴻海": 2, "聯發科": 2,
}


def push_line(msg: str):
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg[:5000]}]},
            timeout=10,
        )
    except Exception as e:
        print(f"[Push] LINE 推播失敗: {e}")


def get_market_summary() -> str:
    """取大盤即時資訊"""
    try:
        r = requests.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw",
            timeout=5, verify=False,
        )
        info = r.json().get("msgArray", [{}])[0]
        index  = info.get("z", "N/A")
        ref    = info.get("y", "N/A")
        d_str  = info.get("d", "")
        t_str  = info.get("t", "")

        try:
            diff = float(index) - float(ref)
            pct  = diff / float(ref) * 100
            sign = "▲" if diff >= 0 else "▼"
            change = f"{sign} {abs(diff):.0f}（{abs(pct):.2f}%）"
        except Exception:
            change = ""

        r2 = requests.get(
            "https://www.twse.com.tw/fund/BFI82U?response=json&type=day",
            timeout=5, verify=False,
        )
        rows = r2.json().get("data", [])
        label_map = {
            "自營商(自行買賣)": "自營", "自營商(避險)": "自營避險",
            "投信": "投信", "外資及陸資(不含外資自營商)": "外資", "合計": "合計",
        }
        law_lines = []
        for row in rows:
            name = row[0].strip()
            if name in label_map:
                amt = int(row[3].replace(",", ""))
                s = "▲" if amt > 0 else "▼"
                law_lines.append(f"  {label_map[name]}：{s} {abs(amt):,}")

        ts = f"{d_str[:4]}/{d_str[4:6]}/{d_str[6:]} {t_str}" if d_str and t_str else ""
        lines = [
            f"📊 加權指數：{index} 點  {change}",
            "",
            "三大法人：",
        ] + law_lines
        if ts:
            lines.append(f"\n📅 {ts}（台北）")
        return "\n".join(lines)
    except Exception as e:
        return f"大盤資料取得失敗：{e}"


def push_premarket():
    """盤前推播（9:00 AM）"""
    now = datetime.now(TAIPEI).strftime("%m/%d")
    summary = get_market_summary()
    push_line(f"🌅 {now} 盤前資訊\n\n{summary}\n\n今日交易注意安全！")
    print("[DailyPush] 盤前推播完成")


def push_postmarket():
    """收盤推播（1:35 PM）"""
    now = datetime.now(TAIPEI).strftime("%m/%d")
    summary = get_market_summary()
    push_line(f"🔔 {now} 收盤整理\n\n{summary}")
    print("[DailyPush] 盤後推播完成")


def get_recent_videos(channel_id: str, n: int = 5) -> list[tuple[str, str]]:
    """用 yt-dlp 抓頻道最近 n 支影片（id, title）"""
    try:
        result = subprocess.run(
            [
                "python3", "-m", "yt_dlp",
                "--dump-json", f"--playlist-items", f"1:{n}", "--no-download",
                "--flat-playlist",
                f"https://www.youtube.com/channel/{channel_id}/videos",
            ],
            capture_output=True, text=True, timeout=90,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                try:
                    d = json.loads(line)
                    videos.append((d.get("id", ""), d.get("title", "")))
                except Exception:
                    pass
        return videos
    except Exception as e:
        print(f"[YouTube] yt-dlp 錯誤: {e}")
    return []


def get_transcript(video_id: str) -> str:
    """取 YouTube 字幕"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id, languages=["zh-TW", "zh-Hans", "zh", "en"])
        return " ".join(t.text for t in transcript)
    except Exception:
        return ""


def find_video_with_transcript(channel_id: str) -> tuple[str, str, str]:
    """找最近有字幕的影片，回傳 (video_id, title, transcript)"""
    videos = get_recent_videos(channel_id, n=5)
    for video_id, title in videos:
        transcript = get_transcript(video_id)
        if transcript:
            return video_id, title, transcript
    # 全部沒字幕，回傳最新影片但 transcript 空
    if videos:
        return videos[0][0], videos[0][1], ""
    return "", "", ""


def ai_summarize(transcript: str, channel_name: str) -> str:
    """用 Groq AI 整理影片重點，格式與截圖一致"""
    if not GROQ_API_KEY:
        return _rule_extract(transcript)
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = f"""以下是台股 YouTuber「{channel_name}」今日影片的逐字稿（部分）：

{transcript[:6000]}

請用繁體中文整理出以下三個重點，每項 1~3 句，簡潔有力：
• 主攻族群：今日討論的主要股票族群或個股
• 重要事件：市場重大消息、法說會、數據
• 操作方向：具體建議、目標價、停損位

只輸出三個重點，不要多餘說明。"""

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Groq] 錯誤: {e}")
        return _rule_extract(transcript)


def _rule_extract(text: str) -> str:
    """備用：規則式萃取"""
    parts = re.split(r"[，。！？\n]", text)
    sentences = [p.strip() for p in parts if len(p.strip()) >= 8]
    scored = []
    for s in sentences:
        score = sum(v for k, v in SCORE_MAP.items() if k in s)
        score += len(re.findall(r"\b\d{4,5}\b", s)) * 2
        if score > 0:
            scored.append((score, s))
    scored.sort(reverse=True)
    return "\n".join(f"• {s}" for _, s in scored[:6])


def push_youtube_summary():
    """每日影片重點推播（約 21:00）"""
    date_str = datetime.now(TAIPEI).strftime("%m/%d")

    for name, channel_id in CHANNELS.items():
        try:
            video_id, title, transcript = find_video_with_transcript(channel_id)
            if not video_id:
                push_line(f"📹 {name} 今日影片找不到，請手動查看")
                continue

            if not transcript:
                push_line(f"📹 {name} {date_str}\n《{title[:50]}》\n\n字幕尚未生成，直接看影片\n🔗 https://youtu.be/{video_id}")
                continue

            summary = ai_summarize(transcript, name)

            msg = f"📹 {name} {date_str} 影片重點\n《{title[:40]}》\n\n"
            msg += summary
            msg += f"\n\n🔗 https://youtu.be/{video_id}"
            push_line(msg)
            print(f"[DailyPush] {name} 影片重點推播完成")

        except Exception as e:
            print(f"[DailyPush] {name} 錯誤: {e}")


# ── 早盤重點推播 ───────────────────────────────────────

_IDX_SYMS = {"^DJI": "道瓊", "^GSPC": "S&P 500", "^IXIC": "那斯達克", "^SOX": "費半"}
_TECH_SYMS = {
    "NVDA": "輝達", "AAPL": "蘋果", "GOOGL": "Google", "MSFT": "微軟",
    "AMZN": "亞馬遜", "AVGO": "博通", "TSLA": "特斯拉", "META": "Meta",
}
_CHINA_SYMS = {
    "BABA": "阿里", "JD": "京東", "BIDU": "百度",
    "NIO": "蔚來", "XPEV": "小鵬", "LI": "理想",
}
_IND_SYMS = {
    "^TNX": ("美債10年", 3), "^VIX": ("恐慌VIX", 2),
    "DX-Y.NYB": ("美元指數", 3), "USDTWD=X": ("美元/台幣", 3), "EWT": ("台灣EWT", 2),
}


def _get_quotes(symbols):
    """取各標的最近收盤和漲跌幅，回傳 {sym: (price, pct)}"""
    import yfinance as yf
    result = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                if prev:
                    result[sym] = (last, (last - prev) / prev * 100)
        except Exception as e:
            print(f"[YF] {sym}: {e}")
    return result


def _fmt_arrow(price, pct, dec=2):
    s = "▲" if pct >= 0 else "▼"
    return f"{price:,.{dec}f}  {s}{abs(pct):.2f}%"


def _make_lines(sym_map, q):
    lines = []
    for sym, val in sym_map.items():
        if sym not in q:
            continue
        name = val if isinstance(val, str) else val[0]
        dec  = 2   if isinstance(val, str) else val[1]
        lines.append(f"{name}({sym}): {_fmt_arrow(q[sym][0], q[sym][1], dec)}")
    return lines


_RSS_SOURCES = [
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",       # MarketWatch 市場即時
    "https://www.cnbc.com/id/15839135/device/rss/rss.html",        # CNBC 股市
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",        # CNBC 經濟
    "https://feeds.marketwatch.com/marketwatch/breaking/",          # MarketWatch 頭條
    "https://finance.yahoo.com/rss/topstories",                     # Yahoo Finance
]


def _rss_articles(url, n=8):
    """從 RSS 抓新聞標題 + 摘要內文"""
    import warnings
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(r.content, "html.parser")
        articles = []
        for item in soup.find_all("item")[:n]:
            title_tag = item.find("title")
            desc_tag  = item.find("description")
            title = title_tag.get_text(strip=True) if title_tag else ""
            desc  = desc_tag.get_text(strip=True)  if desc_tag  else ""
            if title:
                articles.append({"title": title, "desc": desc[:400]})
        return articles
    except Exception as e:
        print(f"[RSS] {url}: {e}")
        return []


def _fetch_news():
    """從多個 RSS 來源抓新聞，合併去重後回傳"""
    seen, articles = set(), []
    for url in _RSS_SOURCES:
        for a in _rss_articles(url):
            if a["title"] not in seen:
                seen.add(a["title"])
                articles.append(a)
        if len(articles) >= 10:
            break
    return articles[:10]


def push_morning_briefing():
    """每日 08:00 盤前重點推播"""
    print("[DailyPush] 準備盤前重點...")
    date_str = datetime.now(TAIPEI).strftime("%m/%d")

    all_syms = list(_IDX_SYMS) + list(_TECH_SYMS) + list(_CHINA_SYMS) + list(_IND_SYMS)
    q = _get_quotes(all_syms)

    idx_lines   = _make_lines(_IDX_SYMS, q)
    tech_lines  = _make_lines(_TECH_SYMS, q)
    china_lines = _make_lines(_CHINA_SYMS, q)
    ind_lines   = _make_lines(_IND_SYMS, q)

    articles = _fetch_news()

    news_block = ""
    if GROQ_API_KEY and articles:
        try:
            from groq import Groq
            snapshot = "\n".join(idx_lines + tech_lines[:4])
            news_text = "\n\n".join(
                f"【{a['title']}】\n{a['desc']}" if a["desc"] else f"【{a['title']}】"
                for a in articles
            )
            prompt = (
                "以下是今日最新財經新聞（標題 + 摘要）：\n\n" + news_text +
                "\n\n美股收盤數據：\n" + snapshot +
                "\n\n請用繁體中文、口語化語氣，整理出 3～4 個最重要的市場新聞重點。"
                "每個重點用一段自然段落（2～3 句），提到具體數字和市場影響。"
                "不要用條列符號，不要用 markdown，段落之間空一行。"
                "最後加一行「今日操作建議」，用 1～2 句白話說明今天該注意什麼。"
                "總字數 350～450 字。"
            )
            resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
            )
            news_block = resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[Groq] 盤前摘要失敗: {e}")

    parts = [f"📌 盤前重點｜{date_str} ☕ 3分鐘速覽"]

    if news_block:
        parts.append(f"⭐️ 重點時事\n{news_block}")
    elif articles:
        parts.append("⭐️ 重點時事\n" + "\n".join(f"• {a['title']}" for a in articles[:5]))

    if idx_lines:
        us = "✅ 美股收盤\n" + "\n".join(idx_lines)
        if tech_lines:
            us += "\n\n科技股\n" + "\n".join(tech_lines)
        if china_lines:
            us += "\n\n中概股\n" + "\n".join(china_lines)
        parts.append(us)

    if ind_lines:
        parts.append("📊 市場指標\n" + "\n".join(ind_lines))

    push_line("\n\n".join(parts)[:4900])
    print("[DailyPush] 盤前重點推播完成")


if __name__ == "__main__":
    # 測試用
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "youtube"
    if cmd == "pre":
        push_premarket()
    elif cmd == "post":
        push_postmarket()
    elif cmd == "morning":
        push_morning_briefing()
    else:
        push_youtube_summary()
