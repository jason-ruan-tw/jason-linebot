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


def smart_extract(text: str, top_n: int = 10) -> list:
    """關鍵句萃取：依關鍵字評分，取高分句子"""
    # 按語意切句
    parts = re.split(r"[，。！？\n]", text)
    sentences = [p.strip() for p in parts if len(p.strip()) >= 8]

    scored = []
    for s in sentences:
        score = sum(v for k, v in SCORE_MAP.items() if k in s)
        score += len(re.findall(r"\b\d{4,5}\b", s)) * 2   # 股票代碼加分
        score += len(re.findall(r"\d+\.?\d*\s*[%元]", s))  # 數字加分
        if score > 0:
            scored.append((score, s))

    scored.sort(reverse=True)
    seen, results = set(), []
    for _, s in scored:
        key = s[:15]
        if key not in seen:
            seen.add(key)
            results.append(s)
        if len(results) >= top_n:
            break
    return results


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

            key_points = smart_extract(transcript)
            if not key_points:
                key_points = [transcript[:200] + "..."]

            msg = f"📹 {name} {date_str} 影片重點\n《{title[:40]}》\n\n"
            msg += "\n".join(f"• {p}" for p in key_points)
            msg += f"\n\n🔗 https://youtu.be/{video_id}"
            push_line(msg)
            print(f"[DailyPush] {name} 影片重點推播完成")

        except Exception as e:
            print(f"[DailyPush] {name} 錯誤: {e}")


if __name__ == "__main__":
    # 測試用
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "youtube"
    if cmd == "pre":
        push_premarket()
    elif cmd == "post":
        push_postmarket()
    else:
        push_youtube_summary()
