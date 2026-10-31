"""watchlist.txt の銘柄について、値段とニュースを集めて docs/data.json に書き出す。"""

import json
import re
import statistics
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "watchlist.txt"
OUTPUT = ROOT / "docs" / "data.json"
JST = timezone(timedelta(hours=9))
NEWS_PER_ITEM = 3
# グラフに付けるニュースのしるし（大きく動いた日のうち、ニュースがあった日）
MARKS_MAX = 4
NEWS_PER_MARK = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# ニュース映像を集める YouTube チャンネル
VIDEO_CHANNELS = ["@TVTOKYOBIZ", "@tbsnewsdig", "@ANNnewsCH", "@ntv_news"]
# 経済専門のチャンネルは、見出しの言葉で選ばずにすべて対象にする
BUSINESS_CHANNELS = {"@TVTOKYOBIZ"}
# 見出しにこれらの言葉が入った動画だけを載せる（登録した銘柄名も自動で加わる）
VIDEO_KEYWORDS = [
    "株", "円安", "円高", "為替", "ドル", "日経平均", "市場", "マーケット", "相場",
    "金利", "日銀", "FRB", "景気", "物価", "決算", "経済",
]
VIDEOS_MAX = 3

# 海外ニュースのライブ配信（表示名, YouTube チャンネル）。同じ表示名は最初に見つかったものを使う
# 画面上部の帯に流す世界の市場（表示名, 記号, 単位）
MARKETS = [
    ("日経平均", "^N225", "円"),
    ("NYダウ", "^DJI", "ドル"),
    ("S&P500", "^GSPC", ""),
    ("ナスダック", "^IXIC", ""),
    ("原油 WTI", "CL=F", "ドル"),
    ("金", "GC=F", "ドル"),
    ("米10年金利", "^TNX", "%"),
    ("ビットコイン", "BTC-USD", "ドル"),
]

# 世界情勢の地図の地域（記号, 表示名, ニュースを探す言葉）
REGIONS = [
    ("us", "アメリカ", "米国 OR アメリカ OR ホワイトハウス OR 米議会 OR 米大統領 OR 米政府 OR ワシントン"),
    ("latam", "中南米", "ブラジル OR メキシコ OR アルゼンチン OR ベネズエラ OR 中南米"),
    ("europe", "ヨーロッパ", "欧州 OR EU OR ドイツ OR フランス OR 英国 OR イギリス OR イタリア OR NATO"),
    ("russia", "ロシア・ウクライナ", "ロシア OR ウクライナ"),
    ("mideast", "中東", "中東 OR イスラエル OR イラン OR ガザ OR サウジ OR レバノン OR シリア OR イエメン OR フーシ"),
    ("africa", "アフリカ", "アフリカ OR ナイジェリア OR エジプト OR スーダン OR エチオピア"),
    ("china", "中国・朝鮮半島・台湾", "中国 OR 台湾 OR 北朝鮮 OR 韓国 OR 香港 OR 習近平"),
    ("asia", "インド・東南アジア", "インド OR 東南アジア OR ASEAN OR インドネシア OR ベトナム OR フィリピン"),
]
REGION_NEWS = 4
# 通貨ペアは、その通貨の地域と自動でつなぐ
CURRENCY_REGIONS = {"USD": "us", "EUR": "europe", "GBP": "europe", "CHF": "europe", "CNY": "china"}
WORLD_TOPIC_URL = "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja"
# 世界情勢から外す話題（株価や個別銘柄のニュース）
NOT_WORLD = ("-株価 -銘柄 -株式 -上場 -アナリスト -決算 -フィギュア -サッカー -野球 -五輪 -選手 -芸能 "
             "-K-POP -アイドル -コンサート -ツアー -観光 -グルメ -ドラマ -映画")

# 今週の予定：海外の経済カレンダー（重要度の高いものだけ使う）
CALENDAR_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json"]
COUNTRY_NAMES = {
    "USD": "米国", "JPY": "日本", "EUR": "ユーロ圏", "GBP": "英国", "CNY": "中国",
    "AUD": "豪州", "CAD": "カナダ", "CHF": "スイス", "NZD": "NZ",
}
EVENT_NAMES = {
    "Non-Farm Employment Change": "雇用統計（非農業部門雇用者数）",
    "Unemployment Rate": "失業率",
    "Average Hourly Earnings m/m": "平均時給（前月比）",
    "CPI m/m": "消費者物価指数（前月比）",
    "CPI y/y": "消費者物価指数（前年比）",
    "Core CPI m/m": "コア消費者物価指数（前月比）",
    "Core CPI y/y": "コア消費者物価指数（前年比）",
    "PPI m/m": "生産者物価指数（前月比）",
    "Core PPI m/m": "コア生産者物価指数（前月比）",
    "Core PCE Price Index m/m": "PCEコア物価指数（前月比）",
    "Retail Sales m/m": "小売売上高（前月比）",
    "Core Retail Sales m/m": "コア小売売上高（前月比）",
    "Advance GDP q/q": "GDP速報値（前期比）",
    "Prelim GDP q/q": "GDP速報値（前期比）",
    "Final GDP q/q": "GDP確報値（前期比）",
    "GDP q/q": "GDP（前期比）",
    "ISM Manufacturing PMI": "ISM製造業景況指数",
    "ISM Services PMI": "ISM非製造業景況指数",
    "Flash Manufacturing PMI": "製造業PMI速報値",
    "Flash Services PMI": "サービス業PMI速報値",
    "Unemployment Claims": "新規失業保険申請件数",
    "JOLTS Job Openings": "JOLTS求人件数",
    "CB Consumer Confidence": "消費者信頼感指数",
    "Prelim UoM Consumer Sentiment": "ミシガン大学消費者信頼感指数（速報）",
    "Federal Funds Rate": "FOMC 政策金利発表",
    "FOMC Statement": "FOMC 声明",
    "FOMC Press Conference": "FOMC 議長会見",
    "FOMC Meeting Minutes": "FOMC 議事録",
    "BOJ Policy Rate": "日銀 政策金利発表",
    "Monetary Policy Statement": "金融政策声明",
    "BOJ Outlook Report": "日銀 展望レポート",
    "BOJ Press Conference": "日銀総裁会見",
    "Tokyo Core CPI y/y": "東京都区部コアCPI（前年比）",
    "National Core CPI y/y": "全国コアCPI（前年比）",
    "Main Refinancing Rate": "ECB 政策金利発表",
    "ECB Press Conference": "ECB 総裁会見",
    "German Flash Manufacturing PMI": "ドイツ製造業PMI速報値",
    "German Flash Services PMI": "ドイツ サービス業PMI速報値",
    "German ifo Business Climate": "ドイツ ifo景況感指数",
    "German Prelim CPI m/m": "ドイツ消費者物価指数速報（前月比）",
    "CPI Flash Estimate y/y": "ユーロ圏消費者物価指数速報（前年比）",
    "Core CPI Flash Estimate y/y": "ユーロ圏コアCPI速報（前年比）",
    "ADP Non-Farm Employment Change": "ADP雇用統計",
    "Durable Goods Orders m/m": "耐久財受注（前月比）",
    "Core Durable Goods Orders m/m": "コア耐久財受注（前月比）",
    "Building Permits": "住宅着工許可件数",
    "Housing Starts": "住宅着工件数",
    "New Home Sales": "新築住宅販売件数",
    "Existing Home Sales": "中古住宅販売件数",
    "Pending Home Sales m/m": "中古住宅販売保留（前月比）",
    "Trade Balance": "貿易収支",
    "Industrial Production m/m": "鉱工業生産（前月比）",
    "Prelim Industrial Production m/m": "鉱工業生産速報（前月比）",
    "Crude Oil Inventories": "原油在庫",
    "Philly Fed Manufacturing Index": "フィラデルフィア連銀製造業指数",
    "Empire State Manufacturing Index": "NY連銀製造業景気指数",
    "Revised UoM Consumer Sentiment": "ミシガン大学消費者信頼感指数（確報）",
    "Final Manufacturing PMI": "製造業PMI確報値",
    "Final Services PMI": "サービス業PMI確報値",
    "Tankan Manufacturing Index": "日銀短観（大企業製造業）",
    "Tankan Non-Manufacturing Index": "日銀短観（大企業非製造業）",
    "SPPI y/y": "企業向けサービス価格指数（前年比）",
    "Retail Sales y/y": "小売売上高（前年比）",
    "Household Spending y/y": "家計支出（前年比）",
    "Prelim GDP Price Index q/q": "GDP物価指数速報（前期比）",
    "Personal Spending m/m": "個人消費支出（前月比）",
    "Personal Income m/m": "個人所得（前月比）",
    "French Flash Manufacturing PMI": "フランス製造業PMI速報値",
    "French Flash Services PMI": "フランス サービス業PMI速報値",
    "Revised UoM Inflation Expectations": "ミシガン大学期待インフレ率（確報）",
    "Advance GDP Price Index q/q": "GDP物価指数速報（前期比）",
    "Employment Cost Index q/q": "雇用コスト指数（前期比）",
    "German Prelim GDP q/q": "ドイツGDP速報（前期比）",
    "Prelim Flash GDP q/q": "ユーロ圏GDP速報（前期比）",
    "Flash GDP q/q": "ユーロ圏GDP速報（前期比）",
    "German Unemployment Change": "ドイツ失業者数",
    "Spanish Flash CPI y/y": "スペイン消費者物価指数速報（前年比）",
    "Italian Prelim CPI m/m": "イタリア消費者物価指数速報（前月比）",
    "French Prelim CPI m/m": "フランス消費者物価指数速報（前月比）",
    "Unemployment Rate (Eurozone)": "ユーロ圏失業率",
    "Chicago PMI": "シカゴ購買部協会景気指数",
    "Final Wholesale Inventories m/m": "卸売在庫（前月比）",
    "Prelim Wholesale Inventories m/m": "卸売在庫速報（前月比）",
    "Core PCE Price Index y/y": "PCEコア物価指数（前年比）",
    "PCE Price Index m/m": "PCE物価指数（前月比）",
    "PCE Price Index y/y": "PCE物価指数（前年比）",
    "Treasury Currency Report": "米財務省 為替報告書",
    "Ivey PMI": "Ivey購買担当者景気指数",
    "Average Cash Earnings y/y": "現金給与総額（前年比）",
    "BOJ Summary of Opinions": "日銀 主な意見",
    "BOJ Core CPI y/y": "日銀版コアCPI（前年比）",
    "Prelim Machine Tool Orders y/y": "工作機械受注速報（前年比）",
    "Core Machinery Orders m/m": "機械受注（前月比）",
    "Bank Holiday": "祝日（市場休み）",
    "Prelim UoM Inflation Expectations": "ミシガン大学期待インフレ率（速報）",
}
SPEAKERS = {"Fed Chair": "FRB議長", "BOJ Gov": "日銀総裁", "ECB President": "ECB総裁", "FOMC Member": "FOMC委員"}

LIVE_CHANNELS = [
    ("Bloomberg", "@markets"),
    ("Bloomberg", "@BloombergTelevision"),
    ("Yahoo Finance", "@YahooFinance"),
    ("Sky News", "@SkyNews"),
    ("DW News", "@dwnews"),
    ("France 24", "@France24_en"),
    ("Al Jazeera", "@aljazeeraenglish"),
    ("CNA", "@channelnewsasia"),
]


def read_watchlist(path):
    """銘柄のリストと、「表示名 → 名前 コード, …」で書いた比べる相手、
    「表示名 の地域 → 地域名, …」で書いた関係する地域を読む。"""
    items, peers, regions = [], {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "→" in line and line.split("→", 1)[0].strip().endswith("の地域"):
            name, rest = (p.strip() for p in line.split("→", 1))
            regions[name[: -len("の地域")].strip()] = [r.strip() for r in re.split(r"[,、，]", rest) if r.strip()]
            continue
        if "→" in line:
            name, rest = (p.strip() for p in line.split("→", 1))
            for part in re.split(r"[,、，]", rest):
                words = part.split()
                if len(words) >= 2:
                    peers.setdefault(name, []).append({"name": " ".join(words[:-1]), "code": words[-1]})
            continue
        parts = [p.strip() for p in re.split(r"[,、，]", line) if p.strip()]
        if len(parts) < 2:
            print(f"読み飛ばし（書き方が違う）: {line}", file=sys.stderr)
            continue
        name, code = parts[0], parts[1]
        keyword = parts[2] if len(parts) > 2 else name
        items.append({"name": name, "code": code, "keyword": keyword})
    return items, peers, regions


def to_symbol(code):
    """証券コードや通貨ペアを、値段の取得先で使う記号に変換する。"""
    code = code.upper().replace(" ", "")
    if re.fullmatch(r"\d{4}|\d{3}[A-Z]", code):
        return code + ".T", "stock"
    m = re.fullmatch(r"([A-Z]{3})/?([A-Z]{3})", code)
    if m:
        return f"{m.group(1)}{m.group(2)}=X", "fx"
    return code, "stock"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def fetch_price(symbol, span="1mo"):
    """値段と、グラフ用のこの1か月の終値。span="1y" なら1年分から「今日の動きの珍しさ」も出す。"""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + f"?range={span}&interval=1d"
    )
    result = json.loads(get(url))["chart"]["result"][0]
    meta = result["meta"]
    closes = result["indicators"]["quote"][0]["close"]
    days = [(datetime.fromtimestamp(t, JST), c) for t, c in zip(result["timestamp"], closes) if c is not None]
    price = meta.get("regularMarketPrice") or days[-1][1]
    # 最後の1本が今日の値なら、その前の日と比べる
    prev = days[-2][1] if len(days) >= 2 else meta.get("chartPreviousClose")
    change = price - prev if prev else None
    percent = change / prev * 100 if prev else None
    month_ago = days[-1][0] - timedelta(days=31)
    return {
        "price": price,
        "change": change,
        "changePercent": percent,
        "currency": meta.get("currency"),
        "history": [{"date": d.strftime("%m/%d"), "day": d.date().isoformat(), "close": c} for d, c in days if d > month_ago],
        "move": move_rarity([c for _, c in days], percent) if span == "1y" else None,
        "year": [{"date": d.strftime("%m/%d"), "day": d.date().isoformat(), "close": c} for d, c in days] if span == "1y" else None,
    }


def fetch_intraday(symbol):
    """直近の取引日の5分ごとの値段（グラフの「1日」用）。"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
           + "?range=1d&interval=5m")
    result = json.loads(get(url))["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [
        {"date": datetime.fromtimestamp(t, JST).strftime("%H:%M"), "close": c}
        for t, c in zip(result.get("timestamp") or [], closes)
        if c is not None
    ]


def move_rarity(closes, percent):
    """今日の動きの大きさを、この1年の毎日の動きと比べる。

    ratio: ふだん（毎日の動きの真ん中の値）の何倍か
    rank:  この1年で何番目に大きい動きか（days 日のうち）
    """
    past = [abs(b / a - 1) * 100 for a, b in zip(closes[:-2], closes[1:-1]) if a]
    if percent is None or len(past) < 20:
        return None
    today = abs(percent)
    typical = statistics.median(past) or 1e-9
    return {
        "ratio": round(today / typical, 2),
        "rank": 1 + sum(c > today for c in past),
        "days": len(past) + 1,
    }


def fetch_news(keyword, days=7):
    """新しい順のニュースをすべて返す（同じ見出しは1つにまとめる）。"""
    return fetch_rss("https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{keyword} when:{days}d", "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    ))


def fetch_rss(url):
    root = ET.fromstring(get(url))
    news = []
    for item in root.iter("item"):
        title = item.findtext("title", "")
        source = item.findtext("source", "")
        # 見出しの末尾についている「 - 新聞社名」を取り除く
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(JST)
        except (TypeError, ValueError):
            published = None
        news.append({
            "title": title,
            "link": item.findtext("link", ""),
            "source": source,
            "published": published.isoformat() if published else None,
        })
    news.sort(key=lambda n: n["published"] or "", reverse=True)
    # 同じ見出しが別の配信元から重ねて届くことがあるので、1つにまとめる
    seen = set()
    return [n for n in news if not (n["title"] in seen or seen.add(n["title"]))]


def fetch_channel_videos(handle):
    """YouTube チャンネルの新しい動画を取得する。"""
    try:
        page = get(f"https://www.youtube.com/{handle}/videos").decode("utf-8", "replace")
    except Exception as e:
        raise ValueError(f"チャンネルのページを開けません（{e}）")
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page)
    if not m:
        raise ValueError("チャンネルが見つかりません")
    try:
        return videos_from_feed(m.group(1))
    except Exception as e:
        print(f"動画 {handle}: 一覧の配信が使えないため、ページから読み取ります（{e}）", file=sys.stderr)
        return videos_from_page(page, handle)


def videos_from_feed(channel_id):
    feed = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    channel = feed.findtext("a:title", "", ns)
    return [
        {
            "id": entry.findtext("yt:videoId", "", ns),
            "title": entry.findtext("a:title", "", ns),
            "channel": channel,
            "published": datetime.fromisoformat(entry.findtext("a:published", "", ns))
            .astimezone(JST)
            .isoformat(),
        }
        for entry in feed.findall("a:entry", ns)
    ]


def videos_from_page(page, handle):
    """チャンネルの動画ページに埋め込まれた一覧から読み取る（投稿日時はおおよそ）。"""
    m = re.search(r'ytInitialData"?\]?\s*=\s*', page)
    if not m:
        raise ValueError("ページに動画の一覧がありません")
    data, _ = json.JSONDecoder().raw_decode(page, m.end())
    m = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    channel = m.group(1) if m else handle
    videos = []

    def add(video_id, title, ago):
        videos.append({
            "id": video_id,
            "title": title,
            "channel": channel,
            "published": (datetime.now(JST) - parse_ago(ago)).isoformat(timespec="minutes"),
        })

    def walk(node):
        if isinstance(node, dict):
            # 以前の書き方
            r = node.get("videoRenderer")
            if r and r.get("videoId"):
                runs = r.get("title", {}).get("runs") or [{}]
                add(r["videoId"], runs[0].get("text", ""), r.get("publishedTimeText", {}).get("simpleText", ""))
                return
            # 新しい書き方
            r = node.get("lockupViewModel")
            if r and r.get("contentId") and "VIDEO" in r.get("contentType", "VIDEO"):
                meta = r.get("metadata", {}).get("lockupMetadataViewModel", {})
                texts = re.findall(r'"content":\s*"([^"]*)"', json.dumps(meta, ensure_ascii=False))
                ago = next((t for t in texts if re.search(r"前|ago", t)), "")
                add(r["contentId"], meta.get("title", {}).get("content", ""), ago)
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return videos


def parse_ago(text):
    """「3時間前」「2 days ago」のような文を、経過時間に直す。"""
    m = re.search(r"(\d+)\s*(秒|分|時間|日|週|か月|ヶ月|年|second|minute|hour|day|week|month|year)", text)
    if not m:
        return timedelta(days=30)
    n, unit = int(m.group(1)), m.group(2)
    seconds = {
        "秒": 1, "second": 1, "分": 60, "minute": 60, "時間": 3600, "hour": 3600,
        "日": 86400, "day": 86400, "週": 604800, "week": 604800,
        "か月": 2592000, "ヶ月": 2592000, "month": 2592000, "年": 31536000, "year": 31536000,
    }[unit]
    return timedelta(seconds=n * seconds)


def fetch_videos(extra_keywords):
    keywords = VIDEO_KEYWORDS + extra_keywords
    videos = []
    for handle in VIDEO_CHANNELS:
        try:
            found = [{**v, "business": handle in BUSINESS_CHANNELS} for v in fetch_channel_videos(handle)]
            print(f"動画 {handle}: {len(found)} 本見つかりました")
            videos += found
        except Exception as e:
            print(f"動画 {handle}: {e}", file=sys.stderr)
    related = [v for v in videos if v.pop("business") or any(k in v["title"] for k in keywords)]
    related.sort(key=lambda v: v["published"], reverse=True)
    return related[:VIDEOS_MAX]


def fetch_live(handle):
    """チャンネルが今ライブ配信中なら、そのチャンネルの番号を返す。

    GitHub のサーバーから見るとロボット確認の画面になり、配信中の動画は特定できない。
    そのためチャンネルの番号だけを渡し、ページ側で「今流しているライブ」を映す。
    """
    page = get(f"https://www.youtube.com/{handle}/live").decode("utf-8", "replace")
    if '"isLive":true' not in page or '"isUpcoming":true' in page:
        return None
    # 確認画面には他のチャンネルの番号も混ざるので、番号はチャンネルのトップページから取る
    top = get(f"https://www.youtube.com/{handle}").decode("utf-8", "replace")
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', top) \
        or re.search(r'"externalId":"(UC[\w-]{22})"', top)
    return {"channel": m.group(1)} if m else None


def fetch_lives():
    lives = []
    for label, handle in LIVE_CHANNELS:
        if any(l["label"] == label for l in lives):
            continue
        try:
            live = fetch_live(handle)
        except Exception as e:
            print(f"ライブ {handle}: {e}", file=sys.stderr)
            continue
        print(f"ライブ {handle}: {'配信中' if live else '配信なし'}")
        if live and all(l["channel"] != live["channel"] for l in lives):
            lives.append({"label": label, **live})
    return lives


def fetch_markets():
    markets = []
    for name, symbol, unit in MARKETS:
        try:
            p = fetch_price(symbol)
        except Exception as e:
            print(f"市場 {name}: {e}", file=sys.stderr)
            continue
        markets.append({"name": name, "unit": unit, **{k: p[k] for k in ("price", "change", "changePercent")}})
    return markets


def fetch_fear():
    """恐怖指数（VIX）。"""
    try:
        p = fetch_price("^VIX")
    except Exception as e:
        print(f"恐怖指数: {e}", file=sys.stderr)
        return None
    return {k: p[k] for k in ("price", "change", "changePercent", "history")}


def fetch_world():
    """Google ニュースの「国際」欄の記事を、見出しの国名で地域に振り分ける。"""
    try:
        topic = fetch_rss(WORLD_TOPIC_URL)
    except Exception as e:
        print(f"世界情勢（国際欄）: {e}", file=sys.stderr)
        topic = []
    print(f"世界情勢: 国際欄 {len(topic)} 件")
    regions = []
    for key, label, query in REGIONS:
        words = [w for w in query.split(" OR ")]
        matched = [n for n in topic if any(w in n["title"] for w in words)]
        news = list(matched)
        if len(news) < REGION_NEWS:
            # 足りない分は言葉で探して補う
            try:
                seen = {n["title"] for n in news}
                news += [n for n in fetch_news(f"{query} {NOT_WORLD}", days=1) if n["title"] not in seen]
            except Exception as e:
                print(f"世界情勢 {label}: {e}", file=sys.stderr)
        regions.append({"key": key, "label": label, "count": len(matched), "news": news[:REGION_NEWS]})
    return regions


def fetch_calendar(currencies):
    events = []
    for url in CALENDAR_URLS:
        try:
            items = json.loads(get(url))
        except Exception as e:
            print(f"予定 {url}: {e}", file=sys.stderr)
            continue
        high = [ev for ev in items if ev.get("impact") == "High"]
        print(f"予定: 全{len(items)}件、重要{len(high)}件（{sorted({ev.get('country') for ev in high})}）、"
              f"期間 {min((ev['date'] for ev in items), default='-')} ～ {max((ev['date'] for ev in items), default='-')}")
        for ev in items:
            if ev.get("impact") not in ("High", "Medium") or ev.get("country") not in currencies:
                continue
            title = ev.get("title", "")
            name = EVENT_NAMES.get(title)
            if not name and title.endswith(" Speaks"):
                name = title[: -len(" Speaks")] + " 発言"
                for en, ja in SPEAKERS.items():
                    name = name.replace(en, ja)
            events.append({
                "date": datetime.fromisoformat(ev["date"]).astimezone(JST).isoformat(timespec="minutes"),
                "country": COUNTRY_NAMES.get(ev["country"], ev["country"]),
                "currency": ev["country"],
                "title": name or title,
                "forecast": ev.get("forecast") or "",
                "previous": ev.get("previous") or "",
                "high": ev.get("impact") == "High",
            })
    events.sort(key=lambda e: e["date"])
    return events


# みんかぶ・株探は自動での読み取りを断るため使わない
EARNINGS_PAGES = ["https://finance.yahoo.co.jp/quote/{code}.T"]


def fetch_earnings_date(code):
    """日本株の決算発表予定日（いくつかの情報サイトを順に試す）。"""
    for url in EARNINGS_PAGES:
        url = url.format(code=code)
        try:
            page = get(url).decode("utf-8", "replace")
        except Exception as e:
            print(f"決算予定 {code}: {url} を開けません（{e}）", file=sys.stderr)
            continue
        m = re.search(r"決算発表(?:予定)?日[^0-9]{0,120}(20\d\d)[/年.-](\d{1,2})[/月.-](\d{1,2})", page)
        if m:
            y, mo, d = map(int, m.groups())
            print(f"決算予定 {code}: {url} から {y}/{mo}/{d}")
            return datetime(y, mo, d, tzinfo=JST).date().isoformat()
        print(f"決算予定 {code}: {url} に予定日が見つかりません", file=sys.stderr)
    return None


def news_marks(entry, news):
    """この1か月で大きく動いた日に、その日のニュースを結びつける。

    日本の株は15時半の取引終了より後のニュースを次の取引日の分として数える。
    """
    history = entry.get("history") or []
    if len(history) < 3:
        return []
    days = [datetime.fromisoformat(h["day"]).date() for h in history]
    changes = [None] + [(b["close"] / a["close"] - 1) * 100 for a, b in zip(history, history[1:])]
    by_day = {}
    for n in news:
        if not n["published"]:
            continue
        t = datetime.fromisoformat(n["published"]).astimezone(JST)
        d = t.date()
        if entry["symbol"].endswith(".T") and (t.hour, t.minute) > (15, 30):
            d += timedelta(days=1)
        # その日以降で最初の取引日に入れる（週末のニュースは月曜の分）
        i = next((i for i, day in enumerate(days) if day >= d), None)
        if i and (days[i] - d).days <= 4:
            by_day.setdefault(i, []).append(n)
    typical = statistics.median(abs(c) for c in changes[1:]) or 1e-9
    picked = sorted((i for i in by_day if abs(changes[i]) >= typical * 1.3), key=lambda i: -abs(changes[i]))[:MARKS_MAX]
    return [
        {
            "date": history[i]["date"],
            "change": round(changes[i], 2),
            "news": [{k: n[k] for k in ("title", "link", "source", "published")} for n in by_day[i][:NEWS_PER_MARK]],
        }
        for i in sorted(picked)
    ]


def main():
    entries = []
    watchlist, peer_lists, region_lists = read_watchlist(WATCHLIST)
    region_keys = {label: key for key, label, _ in REGIONS}
    peer_prices = {}
    for item in watchlist:
        symbol, kind = to_symbol(item["code"])
        entry = {**item, "symbol": symbol, "kind": kind, "errors": [], "peers": [], "regions": []}
        for label in region_lists.get(item["name"], []):
            if label in region_keys:
                entry["regions"].append(region_keys[label])
            else:
                print(f"地域 {label}: 地図にない地域名です（{'、'.join(region_keys)} から選びます）", file=sys.stderr)
        if kind == "fx":
            for c in (symbol[:3], symbol[3:6]):
                key = CURRENCY_REGIONS.get(c)
                if key and key not in entry["regions"]:
                    entry["regions"].append(key)
        for peer in peer_lists.get(item["name"], []):
            peer_symbol = to_symbol(peer["code"])[0]
            if peer_symbol not in peer_prices:
                try:
                    p = fetch_price(peer_symbol)
                    peer_prices[peer_symbol] = {k: p[k] for k in ("price", "change", "changePercent")}
                except Exception as e:
                    print(f"比べる相手 {peer['name']}: {e}", file=sys.stderr)
                    peer_prices[peer_symbol] = None
            if peer_prices[peer_symbol]:
                entry["peers"].append({**peer, "symbol": peer_symbol, **peer_prices[peer_symbol]})
        try:
            entry.update(fetch_price(symbol, "1y"))
            try:
                entry["intraday"] = fetch_intraday(symbol)
            except Exception as e:
                print(f"{item['name']} の1日の値動き: {e}", file=sys.stderr)
        except Exception as e:
            entry["errors"].append("値段を取得できませんでした")
            print(f"{item['name']} の値段: {e}", file=sys.stderr)
        try:
            month_news = fetch_news(item["keyword"], days=31)
            entry["news"] = month_news[:NEWS_PER_ITEM]
            entry["marks"] = news_marks(entry, month_news)
        except Exception as e:
            entry["news"] = []
            entry["errors"].append("ニュースを取得できませんでした")
            print(f"{item['name']} のニュース: {e}", file=sys.stderr)
        entries.append(entry)

    # 予定は、登録した通貨と米国・日本のものを見る
    currencies = {"USD", "JPY"} | {
        c for e in entries if e["kind"] == "fx" for c in (e["symbol"][:3], e["symbol"][3:6])
    }
    earnings = []
    for e in entries:
        if e["kind"] == "stock" and e["symbol"].endswith(".T"):
            try:
                date = fetch_earnings_date(e["symbol"][:-2])
            except Exception as ex:
                print(f"決算予定 {e['name']}: {ex}", file=sys.stderr)
                continue
            if date:
                earnings.append({"name": e["name"], "date": date})

    videos = fetch_videos([w for e in entries for w in (e["name"], e["keyword"])])

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "updated": datetime.now(JST).isoformat(timespec="minutes"),
                "items": entries,
                "videos": videos,
                "lives": fetch_lives(),
                "markets": fetch_markets(),
                "fear": fetch_fear(),
                "world": fetch_world(),
                "calendar": fetch_calendar(currencies),
                "earnings": earnings,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(entries)} 件を書き出しました: {OUTPUT}")


if __name__ == "__main__":
    main()
