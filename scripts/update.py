"""watchlist.txt の銘柄について、値段とニュースを集めて docs/data.json に書き出す。"""

import json
import re
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# ニュース映像を集める YouTube チャンネル
VIDEO_CHANNELS = ["@TVTOKYOBIZ", "@tbsnewsdig", "@ANNnewsCH", "@ntv_news"]
# 見出しにこれらの言葉が入った動画だけを載せる（登録した銘柄名も自動で加わる）
VIDEO_KEYWORDS = [
    "株", "円安", "円高", "為替", "ドル", "日経平均", "市場", "マーケット", "相場",
    "金利", "日銀", "FRB", "景気", "物価", "決算", "経済",
]
VIDEOS_MAX = 3

# 海外ニュースのライブ配信（表示名, YouTube チャンネル）。同じ表示名は最初に見つかったものを使う
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
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,、，]", line) if p.strip()]
        if len(parts) < 2:
            print(f"読み飛ばし（書き方が違う）: {line}", file=sys.stderr)
            continue
        name, code = parts[0], parts[1]
        keyword = parts[2] if len(parts) > 2 else name
        items.append({"name": name, "code": code, "keyword": keyword})
    return items


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


def fetch_price(symbol):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + "?range=1mo&interval=1d"
    )
    result = json.loads(get(url))["chart"]["result"][0]
    meta = result["meta"]
    closes = result["indicators"]["quote"][0]["close"]
    history = [
        {"date": datetime.fromtimestamp(t, JST).strftime("%m/%d"), "close": c}
        for t, c in zip(result["timestamp"], closes)
        if c is not None
    ]
    price = meta.get("regularMarketPrice") or history[-1]["close"]
    # 最後の1本が今日の値なら、その前の日と比べる
    prev = history[-2]["close"] if len(history) >= 2 else meta.get("chartPreviousClose")
    change = price - prev if prev else None
    return {
        "price": price,
        "change": change,
        "changePercent": change / prev * 100 if prev else None,
        "currency": meta.get("currency"),
        "history": history,
    }


def fetch_news(keyword):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{keyword} when:7d", "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    )
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
    unique = [n for n in news if not (n["title"] in seen or seen.add(n["title"]))]
    return unique[:NEWS_PER_ITEM]


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
            found = fetch_channel_videos(handle)
            print(f"動画 {handle}: {len(found)} 本見つかりました")
            videos += found
        except Exception as e:
            print(f"動画 {handle}: {e}", file=sys.stderr)
    related = [v for v in videos if any(k in v["title"] for k in keywords)]
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


def main():
    entries = []
    for item in read_watchlist(WATCHLIST):
        symbol, kind = to_symbol(item["code"])
        entry = {**item, "symbol": symbol, "kind": kind, "errors": []}
        try:
            entry.update(fetch_price(symbol))
        except Exception as e:
            entry["errors"].append("値段を取得できませんでした")
            print(f"{item['name']} の値段: {e}", file=sys.stderr)
        try:
            entry["news"] = fetch_news(item["keyword"])
        except Exception as e:
            entry["news"] = []
            entry["errors"].append("ニュースを取得できませんでした")
            print(f"{item['name']} のニュース: {e}", file=sys.stderr)
        entries.append(entry)

    videos = fetch_videos([w for e in entries for w in (e["name"], e["keyword"])])

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "updated": datetime.now(JST).isoformat(timespec="minutes"),
                "items": entries,
                "videos": videos,
                "lives": fetch_lives(),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(entries)} 件を書き出しました: {OUTPUT}")


if __name__ == "__main__":
    main()
