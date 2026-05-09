#!/usr/bin/env python3
"""
Jason LINE Bot 雙向對話伺服器
支援查股市、個股查詢、說明等指令
"""

import os
import re
import requests
import tempfile
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import yfinance as yf
import mplfinance as mpf
from flask import Flask, request, abort, send_file
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, ImageMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# ── 設定（Render 上透過環境變數注入）──────────────────
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_TOKEN  = os.environ.get("LINE_CHANNEL_TOKEN", "")
# ──────────────────────────────────────────────────

app = Flask(__name__)
handler = WebhookHandler(CHANNEL_SECRET) if CHANNEL_SECRET else None
config  = Configuration(access_token=CHANNEL_TOKEN)


def reply(reply_token: str, text: str):
    with ApiClient(config) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text[:5000])],
            )
        )


# ── 查大盤 ────────────────────────────────────────
def get_market() -> str:
    try:
        r = requests.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw",
            timeout=5, verify=False,
        )
        info = r.json().get("msgArray", [{}])[0]
        index  = info.get("z", "N/A")
        change = info.get("d", "N/A")  # 不一定有，備用

        r2 = requests.get(
            "https://www.twse.com.tw/fund/BFI82U?response=json&type=day",
            timeout=5, verify=False,
        )
        rows = r2.json().get("data", [])
        lines = ["📊 台股即時大盤", f"加權指數：{index} 點", ""]
        lines.append("三大法人買賣超：")
        label_map = {
            "自營商(自行買賣)": "自營(自行)",
            "自營商(避險)": "自營(避險)",
            "投信": "投信",
            "外資及陸資(不含外資自營商)": "外資",
            "合計": "合計",
        }
        for row in rows:
            name = row[0].strip()
            if name in label_map:
                diff = int(row[3].replace(",", ""))
                sign = "▲" if diff > 0 else "▼"
                amt  = f"{abs(diff):,}"
                lines.append(f"  {label_map[name]}：{sign} {amt}")
        return "\n".join(lines)
    except Exception as e:
        return f"查詢失敗：{e}"


# ── 查個股 ────────────────────────────────────────
def get_stock(code: str) -> str:
    try:
        # 先查上市
        for market in ("tse", "otc"):
            r = requests.get(
                f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={market}_{code}.tw",
                verify=False, timeout=5,
            )
            arr = r.json().get("msgArray", [])
            if arr and arr[0].get("z", "-") not in ("-", ""):
                info = arr[0]
                name   = info.get("n", code)
                price  = info.get("z", "N/A")
                open_p = info.get("o", "N/A")
                high   = info.get("h", "N/A")
                low    = info.get("l", "N/A")
                ref    = info.get("y", "N/A")
                vol    = info.get("v", "N/A")
                try:
                    diff = float(price) - float(ref)
                    pct  = diff / float(ref) * 100
                    sign = "▲" if diff >= 0 else "▼"
                    change_str = f"{sign} {abs(diff):.2f}（{abs(pct):.2f}%）"
                except Exception:
                    change_str = ""
                def fmt_p(v):
                    try: return str(int(float(v))) if float(v) == int(float(v)) else f"{float(v):.2f}"
                    except: return v
                return (
                    f"📈 {code} {name}\n"
                    f"現價：{fmt_p(price)}  {change_str}\n"
                    f"開：{fmt_p(open_p)}  高：{fmt_p(high)}  低：{fmt_p(low)}\n"
                    f"昨收：{fmt_p(ref)}  成交量：{vol} 張"
                )
        return f"找不到股票代碼 {code}，請確認是否正確。"
    except Exception as e:
        return f"查詢失敗：{e}"


# ── 台指期夜盤 ────────────────────────────────────
def get_tw_night() -> str:
    try:
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://mis.taifex.com.tw",
            "Referer": "https://mis.taifex.com.tw/futures/",
        }
        r = requests.post(
            "https://mis.taifex.com.tw/futures/api/getQuoteList",
            json={"MarketType": "1", "CommodityID": "TX"},
            headers=headers, verify=False, timeout=10,
        )
        contracts = r.json()["RtData"]["QuoteList"]

        # 取成交量最大的合約（排除現貨和價差）
        main = max(
            [c for c in contracts if "-M" in c["SymbolID"] and "/" not in c["SymbolID"] and c["CTotalVolume"]],
            key=lambda c: int(c["CTotalVolume"] or 0),
        )
        price = main["CLastPrice"]
        ref   = main["CRefPrice"]
        diff  = float(main["CDiff"])
        pct   = float(main["CDiffRate"])
        vol   = main["CTotalVolume"]
        t     = main["CTime"]
        name  = main["DispCName"]
        sign  = "▲" if diff >= 0 else "▼"
        time_fmt = f"{t[:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else t

        lines = [
            f"🌙 台指期夜盤（{name}）",
            f"最新：{price}  {sign}{abs(diff):.0f}（{abs(pct):.2f}%）",
            f"參考：{ref}  成交量：{vol} 口",
            f"更新：{time_fmt}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"台指期夜盤查詢失敗：{e}"


# ── 查美股（含夜盤）────────────────────────────────
def _fetch_us_quote(sym: str) -> dict:
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
        params={"includePrePost": "true", "interval": "1m", "range": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        verify=False, timeout=6,
    )
    return r.json()["chart"]["result"][0]["meta"]


def _format_us_line(name: str, meta: dict, night: bool = False) -> str:
    reg_price = meta.get("regularMarketPrice", 0)
    prev      = meta.get("chartPreviousClose") or meta.get("previousClose", 0)
    pre_price = meta.get("preMarketPrice")
    post_price = meta.get("postMarketPrice")
    pre_chg   = meta.get("preMarketChange")
    post_chg  = meta.get("postMarketChange")

    def fmt(price, chg, prev_p):
        if not price:
            return None
        diff = chg if chg is not None else (price - prev_p if prev_p else 0)
        pct  = diff / prev_p * 100 if prev_p else 0
        sign = "▲" if diff >= 0 else "▼"
        return f"{price:,.2f}  {sign}{abs(diff):.2f}（{abs(pct):.2f}%）"

    if night:
        # 夜盤優先顯示盤前/盤後
        if post_price:
            val = fmt(post_price, post_chg, reg_price)
            return f"{name}（盤後）：{val}" if val else f"{name}：無夜盤"
        elif pre_price:
            val = fmt(pre_price, pre_chg, reg_price)
            return f"{name}（盤前）：{val}" if val else f"{name}：無夜盤"
        else:
            return f"{name}：夜盤尚未開始"
    else:
        val = fmt(reg_price, None, prev)
        return f"{name}：{val}" if val else f"{name}：資料暫無"


def get_us_market() -> str:
    symbols = {"^DJI": "道瓊", "^GSPC": "S&P 500", "^IXIC": "那斯達克",
               "NVDA": "NVIDIA", "TSM": "台積電 ADR", "AAPL": "Apple"}
    lines = ["🌏 美股即時行情"]
    try:
        for sym, name in symbols.items():
            meta = _fetch_us_quote(sym)
            lines.append(_format_us_line(name, meta, night=False))
    except Exception as e:
        return f"美股查詢失敗：{e}"
    return "\n".join(lines)


def get_us_night() -> str:
    symbols = {"NVDA": "NVIDIA", "TSM": "台積電 ADR", "AAPL": "Apple",
               "^DJI": "道瓊", "^GSPC": "S&P 500", "^IXIC": "那斯達克"}
    lines = ["🌙 美股夜盤（盤前/盤後）"]
    has_data = False
    try:
        for sym, name in symbols.items():
            meta = _fetch_us_quote(sym)
            line = _format_us_line(name, meta, night=True)
            lines.append(line)
            if "夜盤尚未開始" not in line and "無夜盤" not in line:
                has_data = True
    except Exception as e:
        return f"美股夜盤查詢失敗：{e}"
    if not has_data:
        lines.append("\n（目前為美股正常交易時段或市場休市，夜盤資料在收盤後 / 開盤前才會出現）")
    return "\n".join(lines)


# ── 技術圖 ───────────────────────────────────────
CHART_DIR = "/tmp/jason_charts"
os.makedirs(CHART_DIR, exist_ok=True)


def get_base_url() -> str:
    """取得伺服器對外 URL：Render 用環境變數，本機用 ngrok"""
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if render_url:
        return render_url.rstrip("/")
    # 本機開發：試著抓 ngrok
    try:
        r = requests.get("http://localhost:4040/api/tunnels", timeout=3)
        return r.json()["tunnels"][0]["public_url"]
    except Exception:
        return ""


_tw_style = mpf.make_mpf_style(
    marketcolors=mpf.make_marketcolors(
        up='red', down='green',
        wick={'up': 'red', 'down': 'green'},
        edge={'up': 'red', 'down': 'green'},
        volume={'up': 'red', 'down': 'green'},
    ),
    gridstyle='--', gridcolor='#e0e0e0',
    facecolor='white', figcolor='white',
)


def _patch_tw_today(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """用 TWSE 即時資料補上今日最新一筆（若 yfinance 尚未更新）"""
    try:
        r = requests.get(
            f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{symbol}.tw",
            verify=False, timeout=4,
        )
        info = r.json().get("msgArray", [{}])[0]
        d = info.get("d", "")          # 格式 20260508
        t_str = info.get("t", "")      # 格式 13:30:00
        o = info.get("o"); h = info.get("h")
        l = info.get("l"); c = info.get("z"); v = info.get("v")
        if not all([d, o, h, l, c, v]):
            return df
        dt = pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:8]}", tz="Asia/Taipei")
        row = pd.DataFrame([{
            "Open":   float(o), "High":   float(h),
            "Low":    float(l), "Close":  float(c),
            "Volume": float(v) * 1000,
        }], index=[dt])
        # 若最後一筆是同一天的 NaN，替換；否則直接 append
        if len(df) and df.index[-1].date() == dt.date():
            df = df.iloc[:-1]
        df = pd.concat([df, row])
    except Exception:
        pass
    return df


def make_chart(symbol: str):
    """產生 K 線圖（台灣色系：紅漲綠跌），回傳本地檔案路徑"""
    try:
        is_tw = bool(re.match(r"^\d{4,6}$", symbol))
        yf_sym = f"{symbol}.TW" if is_tw else symbol

        t = yf.Ticker(yf_sym)
        df = t.history(period="3mo", interval="1d", auto_adjust=True)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 台股補今日即時資料
        if is_tw:
            df = _patch_tw_today(df, symbol)

        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df = df.tail(60)
        if len(df) < 5:
            return None

        out = os.path.join(CHART_DIR, f"{symbol}.png")
        mpf.plot(df, type='candle', volume=True,
            mav=(5, 20, 60),
            style=_tw_style,
            title=f"{symbol}  60D  MA5/20/60",
            ylabel='Price', ylabel_lower='Vol',
            figsize=(10, 6), savefig=out)
        return out
    except Exception as e:
        print(f"[Chart error] {symbol}: {e}")
        return None


def reply_image(reply_token: str, image_url: str):
    with ApiClient(config) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[ImageMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url,
                )],
            )
        )


def reply_with_chart(reply_token: str, text: str, symbol: str):
    """同時回覆文字和技術圖"""
    ngrok = get_base_url()
    messages = [TextMessage(text=text[:5000])]
    if ngrok:
        path = make_chart(symbol)
        if path:
            url = f"{ngrok}/chart/{symbol}"
            messages.append(ImageMessage(
                original_content_url=url,
                preview_image_url=url,
            ))
    with ApiClient(config) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


# ── 說明 ──────────────────────────────────────────
HELP_TEXT = """\
📋 Jason Bot 指令說明

查台股大盤：
  查股市 / 大盤 / 台股

查台指期夜盤：
  台股夜盤 / 夜盤 / 台指期

查美股大盤：
  查美股 / 美股 / 美國

查美股夜盤（盤前/盤後）：
  美股夜盤 / 美夜盤

查個股（輸入股票代碼）：
  2330 → 台積電
  2317 → 鴻海
  0050 → 元大台灣50

查技術圖（60日K線+均線+量）：
  圖 2330 → 台積電技術圖
  圖 NVDA → NVIDIA技術圖
  AAPL 圖 → Apple技術圖

其他功能陸續更新中 💪"""


# ── Webhook ───────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    if handler:
        try:
            handler.handle(body, signature)
        except InvalidSignatureError:
            abort(400)
    else:
        # 開發模式：不驗簽
        import json
        events = json.loads(body).get("events", [])
        for event in events:
            if event.get("type") == "message" and event["message"]["type"] == "text":
                process_text(event["replyToken"], event["message"]["text"])

    return "OK"


def process_text(reply_token: str, text: str):
    print(f"[MSG] raw={repr(text)}")
    text = text.strip()

    # 技術圖：「圖 2330」或「NVDA 圖」
    chart_match = re.match(r"^圖\s*(\S+)$|^(\S+)\s*圖$", text)
    if chart_match:
        symbol = (chart_match.group(1) or chart_match.group(2)).upper()
        ngrok = get_base_url()
        if not ngrok:
            reply(reply_token, "圖表服務暫時無法使用，請稍後再試。")
            return
        path = make_chart(symbol)
        if not path:
            reply(reply_token, f"找不到 {symbol} 的資料，請確認代碼是否正確。")
            return
        url = f"{ngrok}/chart/{symbol}"
        print(f"[Chart] sending image: {url}")
        try:
            reply_image(reply_token, url)
            print(f"[Chart] success")
        except Exception as e:
            print(f"[Chart] error: {e}")
            reply(reply_token, f"圖表產生成功但傳送失敗：{e}")
        return

    if re.match(r"^(查股市|大盤|台股)$", text):
        reply_with_chart(reply_token, get_market(), "^TWII")
    elif re.match(r"^(台股夜盤|夜盤|台指期)$", text):
        reply(reply_token, get_tw_night())
    elif re.match(r"^(查美股|美股|美國)$", text):
        reply_with_chart(reply_token, get_us_market(), "^DJI")
    elif re.match(r"^(美股夜盤|美夜盤)$", text):
        reply(reply_token, get_us_night())
    elif re.match(r"^\d{4,6}$", text):
        reply_with_chart(reply_token, get_stock(text), text)
    elif re.match(r"^(說明|help|Help|HELP)$", text):
        reply(reply_token, HELP_TEXT)
    else:
        reply(reply_token, f"收到：「{text}」\n\n傳「說明」查看可用指令。")


if handler:
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event):
        process_text(event.reply_token, event.message.text)


@app.route("/chart/<symbol>")
def chart(symbol: str):
    path = make_chart(symbol)
    if path and os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "圖表生成失敗", 404


@app.route("/", methods=["GET"])
def index():
    return "Jason LINE Bot 運行中 ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
