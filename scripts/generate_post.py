#!/usr/bin/env python3
"""ブログ記事をClaude APIで自動生成して _posts/ に保存するスクリプト。

GitHub Actions から定期実行される想定。ローカルで試す場合:

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/generate_post.py

API を呼ばずにプロンプトだけ確認する場合:

    python scripts/generate_post.py --dry-run
"""

import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "_posts"
SETTINGS_PATH = ROOT / "config" / "settings.yml"
TOPICS_PATH = ROOT / "config" / "topics.yml"

JST = datetime.timezone(datetime.timedelta(hours=9))

PR_NOTICE = "※本記事はアフィリエイト広告(PR)を含みます。"

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "記事タイトル(28〜35文字。狙う検索キーワードを先頭寄りに含める)"},
        "slug": {"type": "string", "description": "URL用スラッグ。英小文字・数字・ハイフンのみ、内容を表す英単語2〜4語(例: ai-chat-comparison)"},
        "description": {"type": "string", "description": "meta description用の概要(80〜110文字。検索キーワードを含め、クリックしたくなる文にする)"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "記事タグ(2〜4個、日本語)"},
        "body": {"type": "string", "description": "Markdown形式の記事本文。タイトル(h1)は含めず、## 見出しから始める"},
    },
    "required": ["title", "slug", "description", "tags", "body"],
    "additionalProperties": False,
}

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topics"],
    "additionalProperties": False,
}


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def existing_post_titles() -> list[str]:
    """_posts/ 内の既存記事タイトルを front matter から集める(重複回避用)。"""
    titles = []
    if not POSTS_DIR.exists():
        return titles
    for post in sorted(POSTS_DIR.glob("*.md")):
        text = post.read_text(encoding="utf-8")
        m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
        if m:
            titles.append(m.group(1))
    return titles


def build_article_prompt(topic: str, settings: dict, titles: list[str]) -> str:
    article = settings["article"]
    titles_block = "\n".join(f"- {t}" for t in titles) if titles else "(まだありません)"
    return f"""あなたは日本語のAIツール・ガジェット紹介ブログのライターです。
以下のトピックについて、ブログ記事を1本書いてください。

# トピック
{topic}

# 文体・構成の指示
{article['tone']}
- 文字数は{article['min_chars']}〜{article['max_chars']}字程度。
- 読者が具体的な行動(ツールを試す・商品を検討する)に移れる実用的な内容にする。
- 正確性に自信のない具体的な価格・数値は断定せず、「公式サイトで最新情報を確認してください」と促す。
- 記事の最後に「まとめ」セクションを置く。

# SEOの指示
- このトピックで検索する人が使いそうなキーワードを1つ想定し、タイトル・最初の見出し・本文の冒頭段落に自然に含める。
- 冒頭の1〜2段落で「この記事を読むと何がわかるか」を明示し、検索意図(知りたいこと)に最初に答える。
- 見出しは ## と ### の階層を守り、見出しだけ読んでも記事の流れがわかるようにする。
- 読者が次に検索しそうな疑問(例:「〜は無料で使える?」)を1つ以上、見出しまたはQ&A形式で先回りして解消する。
- キーワードの不自然な詰め込みはしない。あくまで読者にとって自然な日本語を最優先する。

# アフィリエイトリンクの挿入
商品やサービスを紹介する自然な箇所(1〜3箇所)に、次の形式のプレースホルダを単独行で挿入してください:
- Amazonで探せる商品: {{{{AMAZON:検索キーワード}}}}
- 楽天で探せる商品: {{{{RAKUTEN:検索キーワード}}}}
検索キーワードは「ワイヤレスイヤホン ノイズキャンセリング」のような具体的な語にしてください。
物理的な商品が登場しない記事では無理に挿入しなくて構いません。

# 既存記事(内容の重複を避けてください)
{titles_block}
"""


def build_refill_prompt(settings: dict, titles: list[str], pending: list[str]) -> str:
    count = settings["topics"]["refill_count"]
    used = "\n".join(f"- {t}" for t in titles + pending) or "(まだありません)"
    return f"""日本語のAIツール・ガジェット紹介ブログの記事トピックを{count}個提案してください。

条件:
- 検索需要がありそうな、具体的で実用的なトピック(比較・選び方・活用法など)
- アフィリエイト収益(Amazon・楽天・ツール紹介)につながりやすいもの
- 以下の既存トピックと重複しないこと

# 既存トピック
{used}
"""


def call_claude(client, model: str, prompt: str, schema: dict, max_tokens: int = 16000) -> dict:
    """構造化出力で記事/トピックをJSONとして受け取る。長文出力のためストリーミングを使う。"""
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


def replace_affiliate_placeholders(body: str, settings: dict) -> str:
    affiliate = settings.get("affiliate") or {}
    amazon_tag = (affiliate.get("amazon_tag") or "").strip()

    def amazon_link(m: re.Match) -> str:
        keyword = m.group(1).strip()
        url = f"https://www.amazon.co.jp/s?k={urllib.parse.quote(keyword)}"
        if amazon_tag:
            url += f"&tag={urllib.parse.quote(amazon_tag)}"
        return f"👉 [Amazonで「{keyword}」を見る]({url})"

    def rakuten_link(m: re.Match) -> str:
        keyword = m.group(1).strip()
        url = f"https://search.rakuten.co.jp/search/mall/{urllib.parse.quote(keyword)}/"
        return f"👉 [楽天市場で「{keyword}」を探す]({url})"

    body = re.sub(r"\{\{AMAZON:([^}]+)\}\}", amazon_link, body)
    body = re.sub(r"\{\{RAKUTEN:([^}]+)\}\}", rakuten_link, body)
    return body


def sanitize_slug(slug: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    return slug or fallback


def render_post(article: dict, settings: dict, date: datetime.date) -> str:
    body = replace_affiliate_placeholders(article["body"].strip(), settings)
    tags = json.dumps(article["tags"], ensure_ascii=False)
    title = article["title"].replace('"', "'")
    description = article["description"].replace('"', "'")
    return f"""---
layout: post
title: "{title}"
description: "{description}"
date: {date.isoformat()}
tags: {tags}
---

{PR_NOTICE}

{body}
"""


def refill_topics_if_needed(client, model: str, settings: dict, topics: dict, titles: list[str], dry_run: bool) -> None:
    pending = topics.get("pending") or []
    threshold = settings["topics"]["refill_threshold"]
    if len(pending) > threshold:
        return
    print(f"残りトピックが{len(pending)}本のため補充します...")
    if dry_run:
        print("(dry-run のため補充をスキップ)")
        return
    prompt = build_refill_prompt(settings, titles, pending)
    result = call_claude(client, model, prompt, TOPICS_SCHEMA, max_tokens=4096)
    new_topics = [t for t in result["topics"] if t not in pending]
    topics["pending"] = pending + new_topics
    print(f"{len(new_topics)}本のトピックを補充しました。")


def main() -> int:
    parser = argparse.ArgumentParser(description="ブログ記事を自動生成します")
    parser.add_argument("--dry-run", action="store_true", help="APIを呼ばずプロンプトだけ表示する")
    args = parser.parse_args()

    settings = load_yaml(SETTINGS_PATH)
    topics = load_yaml(TOPICS_PATH)
    model = settings["model"]
    titles = existing_post_titles()

    pending = topics.get("pending") or []
    if not pending:
        print("エラー: トピックが空です。config/topics.yml の pending にトピックを追加してください。", file=sys.stderr)
        return 1

    topic = pending[0]
    today = datetime.datetime.now(JST).date()
    prompt = build_article_prompt(topic, settings, titles)

    print(f"モデル: {model}")
    print(f"トピック: {topic}")

    if args.dry_run:
        print("\n===== 記事生成プロンプト(dry-run) =====\n")
        print(prompt)
        return 0

    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "エラー: ANTHROPIC_API_KEY が設定されていません。\n"
            "GitHubリポジトリの Settings → Secrets and variables → Actions で\n"
            "Name: ANTHROPIC_API_KEY / Secret: APIキー(sk-ant-...) を登録してください。\n"
            "詳しい手順は README.md の「セットアップ手順」を参照してください。",
            file=sys.stderr,
        )
        return 1

    import anthropic  # APIキー不要のdry-runでも動くよう遅延インポート

    client = anthropic.Anthropic()

    article = call_claude(client, model, prompt, ARTICLE_SCHEMA)
    slug = sanitize_slug(article["slug"], fallback=f"post-{today.strftime('%Y%m%d')}")
    post_path = POSTS_DIR / f"{today.isoformat()}-{slug}.md"
    if post_path.exists():
        post_path = POSTS_DIR / f"{today.isoformat()}-{slug}-2.md"

    POSTS_DIR.mkdir(exist_ok=True)
    post_path.write_text(render_post(article, settings, today), encoding="utf-8")
    print(f"記事を保存しました: {post_path.relative_to(ROOT)}")

    # 使い終わったトピックを done に移動
    topics["pending"] = pending[1:]
    topics.setdefault("done", []).append(topic)

    refill_topics_if_needed(client, model, settings, topics, titles + [article["title"]], args.dry_run)
    save_yaml(TOPICS_PATH, topics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
