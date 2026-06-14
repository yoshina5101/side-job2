#!/usr/bin/env python3
"""新しく公開した記事をXに自動投稿するスクリプト。

「メインツイート(フック文・リンクなし)+ リプライにブログURL」の形式で投稿する。
本文にリンクを入れると表示が抑制されるため、リンクはリプに回す。

GitHub Actions から、記事生成・コミットの後に実行される想定。

    python scripts/post_to_x.py            # その日の新規記事を投稿
    python scripts/post_to_x.py --dry-run  # 投稿せずツイート文だけ表示
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

import generate_post as g

X_POSTED_PATH = g.ROOT / "config" / "x_posted.yml"

TWEET_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Xに投稿するフック型ツイート本文。120字以内。URLは含めない。ハッシュタグは付けても1個まで。末尾は読者を本文へ誘う表現(例: 🔽)で締める。",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


def site_url() -> str:
    """_config.yml の url を読む(末尾スラッシュなし)。"""
    text = (g.ROOT / "_config.yml").read_text(encoding="utf-8")
    m = re.search(r'^url:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    return (m.group(1) if m else "").rstrip("/")


def parse_front_matter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    fm = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        km = re.match(r'^(\w+):\s*["\']?(.+?)["\']?\s*$', line)
        if km:
            fm[km.group(1)] = km.group(2)
    return fm


def build_tweet(client, model: str, title: str, description: str) -> str:
    prompt = f"""日本語のAIツール・ガジェットブログの新着記事を告知するXツイートを1つ作ってください。

# 記事タイトル
{title}

# 記事の概要
{description}

# 条件
- 読者が「続きを読みたい」と思うフック型の本文。120字以内。
- URLは含めない(リンクは別途リプに貼るため)。
- ハッシュタグは付けても1個まで。絵文字は1〜2個までで自然に。
- 末尾は本文へ誘う表現(例: 🔽 や 詳しくは↓)で締める。
- 煽りすぎず、初心者にやさしいトーンで。"""
    result = g.call_claude(client, model, prompt, TWEET_SCHEMA, max_tokens=1024)
    return result["text"].strip()


def post_pair(text: str, url: str) -> None:
    """メインツイート + リプにURL を投稿する。"""
    import tweepy

    api = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    main = api.create_tweet(text=text)
    tweet_id = main.data["id"]
    api.create_tweet(text=f"詳しくはこちら👇\n{url}", in_reply_to_tweet_id=tweet_id)


def has_credentials() -> bool:
    return all(
        os.environ.get(k)
        for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="新着記事をXに自動投稿します")
    parser.add_argument("--dry-run", action="store_true", help="投稿せずツイート文を表示する")
    args = parser.parse_args()

    settings = g.load_yaml(g.SETTINGS_PATH)
    x_cfg = settings.get("x") or {}
    if not x_cfg.get("enabled"):
        print("X自動投稿は無効です(config/settings.yml の x.enabled が false)。")
        return 0

    model = settings["model"]
    base = site_url()

    # 投稿済みリストを読む
    state = g.load_yaml(X_POSTED_PATH) if X_POSTED_PATH.exists() else {}
    posted = state.get("posted") or []

    # その日付の新規記事だけを対象にする(初回有効化で全記事を投稿しない安全策)
    today = datetime.datetime.now(g.JST).date().isoformat()
    candidates = sorted(g.POSTS_DIR.glob(f"{today}-*.md"))
    targets = [p for p in candidates if p.name not in posted]
    if not targets:
        print("本日の新規投稿対象はありません。")
        return 0

    if not args.dry_run:
        if not has_credentials():
            print("エラー: X APIの認証情報(X_API_KEY 等)が未設定です。", file=sys.stderr)
            return 1
        import anthropic
        client = anthropic.Anthropic()
    else:
        client = None

    for post in targets:
        fm = parse_front_matter(post.read_text(encoding="utf-8"))
        title = fm.get("title", "")
        description = fm.get("description", "")
        url = base + g.post_url_from_filename(post.name)
        try:
            if args.dry_run:
                print(f"--- {post.name} ---")
                print(f"(dry-run: ツイート文はAPI未呼び出しのため省略)")
                print(f"URL: {url}\n")
                continue
            text = build_tweet(client, model, title, description)
            post_pair(text, url)
            posted.append(post.name)
            state["posted"] = posted
            g.save_yaml(X_POSTED_PATH, state)  # 1件ごとに保存し二重投稿を防ぐ
            print(f"投稿しました: {post.name}\n  {text}")
        except Exception as e:  # 1件の失敗で全体を止めない
            print(f"投稿に失敗しました({post.name}): {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
