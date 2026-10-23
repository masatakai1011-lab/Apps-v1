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
NEWS_PER_ITEM = 5
USER_AGENT = "Mozilla/5.0 (compatible; market-board/1.0)"

# ニュース映像を集める YouTube チャンネル
VIDEO_CHANNELS = ["@TVTOKYOBIZ", "@tbsnewsdig", "@ANNnewsCH", "@ntv_news"]
# 見出しにこれらの言葉が入った動画だけを載せる（登録した銘柄名も自動で加わる）
VIDEO_KEYWORDS = [
    "株", "円安", "円高", "為替", "ドル", "日経平均", "市場", "マーケット", "相場",
    "金利", "日銀", "FRB", "景気", "物価", "決算", "経済",
]
VIDEOS_MAX = 8


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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
    """YouTube チャンネルの新しい動画（最大15本）を取得する。"""
    page = get(f"https://www.youtube.com/{handle}").decode("utf-8", "replace")
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page)
    if not m:
        raise ValueError("チャンネルが見つかりません")
    feed = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"))
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    channel = feed.findtext("a:title", handle, ns)
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


def fetch_videos(extra_keywords):
    keywords = VIDEO_KEYWORDS + extra_keywords
    videos = []
    for handle in VIDEO_CHANNELS:
        try:
            videos += fetch_channel_videos(handle)
        except Exception as e:
            print(f"動画 {handle}: {e}", file=sys.stderr)
    related = [v for v in videos if any(k in v["title"] for k in keywords)]
    related.sort(key=lambda v: v["published"], reverse=True)
    return related[:VIDEOS_MAX]


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
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(entries)} 件を書き出しました: {OUTPUT}")


if __name__ == "__main__":
    main()
