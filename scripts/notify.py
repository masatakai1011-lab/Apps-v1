"""大きく動いた銘柄を、GitHub の「お知らせ（Issue）」にして持ち主に届ける。

同じ銘柄・同じ日のお知らせは1回だけ。作ったお知らせはすぐ閉じる（通知は届く）。
GitHub Actions の中で動かす（GH_TOKEN と GITHUB_REPOSITORY が必要）。
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALERTS = ROOT / "alerts.json"
API = "https://api.github.com"


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read() or "null")


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", repo.split("/")[0])
    alerts = json.loads(ALERTS.read_text(encoding="utf-8")) if ALERTS.exists() else []
    if os.environ.get("TEST_ALERT") == "true":
        alerts.append({"title": "【テスト】株・FX監視のお知らせ", "body": "お知らせが届くかどうかの確認です。これが届いていれば、大きく動いたときにも同じように届きます。"})
    if not alerts:
        print("お知らせはありません")
        return
    recent = {i["title"] for i in call("GET", f"/repos/{repo}/issues?state=all&per_page=100&sort=created&direction=desc")}
    for a in alerts:
        if a["title"] in recent and not a["title"].startswith("【テスト】"):
            print(f"送信済み: {a['title']}")
            continue
        issue = call("POST", f"/repos/{repo}/issues", {"title": a["title"], "body": f"@{owner}\n\n{a['body']}"})
        call("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
        print(f"お知らせしました: {a['title']}（#{issue['number']}）")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"お知らせできませんでした: {e}", file=sys.stderr)
        sys.exit(1)
