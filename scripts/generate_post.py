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
CATEGORIES_PATH = ROOT / "_data" / "categories.yml"

JST = datetime.timezone(datetime.timedelta(hours=9))

PR_NOTICE = "※本記事はアフィリエイト広告(PR)を含みます。"

def load_category_names() -> list[str]:
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        return [c["name"] for c in yaml.safe_load(f)]


CATEGORY_NAMES = load_category_names()

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "記事タイトル(28〜35文字。狙う検索キーワードを先頭寄りに含める)"},
        "slug": {"type": "string", "description": "URL用スラッグ。英小文字・数字・ハイフンのみ、内容を表す英単語2〜4語(例: ai-chat-comparison)"},
        "description": {"type": "string", "description": "meta description用の概要(80〜110文字。検索キーワードを含め、クリックしたくなる文にする)"},
        "category": {"type": "string", "enum": CATEGORY_NAMES, "description": "記事に最も合うカテゴリーを1つ選ぶ"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "記事タグ(2〜4個、日本語)"},
        "body": {"type": "string", "description": "Markdown形式の記事本文。タイトル(h1)は含めず、## 見出しから始める"},
    },
    "required": ["title", "slug", "description", "category", "tags", "body"],
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


def post_url_from_filename(name: str) -> str | None:
    """ファイル名(YYYY-MM-DD-slug.md)から permalink を組み立てる。"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.+)\.md$", name)
    if not m:
        return None
    y, mo, d, slug = m.groups()
    return f"/{y}/{mo}/{d}/{slug}/"


def existing_posts_meta() -> list[dict]:
    """既存記事のタイトル・URL・カテゴリーを集める(内部リンクの候補用)。"""
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for post in sorted(POSTS_DIR.glob("*.md")):
        url = post_url_from_filename(post.name)
        if not url:
            continue
        text = post.read_text(encoding="utf-8")
        title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
        cat_m = re.search(r"^category:\s*(.+?)\s*$", text, re.MULTILINE)
        if title_m:
            posts.append({
                "title": title_m.group(1),
                "url": url,
                "category": cat_m.group(1) if cat_m else "",
            })
    return posts


def internal_links_block(posts_meta: list[dict]) -> str:
    if not posts_meta:
        return "(まだありません)"
    return "\n".join(f"- [{p['title']}]({p['url']}) — カテゴリー: {p['category']}" for p in posts_meta)


INTERNAL_LINK_INSTRUCTION = """# 内部リンクの挿入(重要)
読者の役に立つ箇所で、下の「サイト内の既存記事」のうち関連するものへのリンクを本文中に2〜3個、Markdown形式で自然に挿入してください。
- リンク先URLは下のリストにある正確なものをそのままコピーして使う(URLを創作しない)。
- 「詳しくは〜をご覧ください」のように、文章の流れの中で自然に差し込む。
- 関連性の低い記事を無理にリンクしない。該当が無ければ少なくて構いません。

# サイト内の既存記事(内部リンク候補・内容の重複も避ける)
{links}
"""


def build_article_prompt(topic: str, settings: dict, posts_meta: list[dict]) -> str:
    article = settings["article"]
    links = internal_links_block(posts_meta)
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

# 図解の挿入
記事の理解を助ける箇所に、Mermaid記法の図解を1つ入れてください(選び方のフローチャート、手順の流れ、比較の構造などが向いています)。
- ```mermaid のコードブロックで囲み、graph TD または graph LR を使う。
- ノードは8個以内。ラベルは短い日本語にし、ラベル内に括弧・引用符・カンマなどの記号を使わない。
- 構文エラーを避けるため、ノードIDは英数字(A、B、C1など)にする。
- 図解にすると不自然な内容の記事では、無理に入れなくて構いません。

{INTERNAL_LINK_INSTRUCTION.format(links=links)}"""


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


def moshimo_wrap(target_url: str, template: str) -> str:
    """もしも「どこでもリンク」のURLを流用し、リンク先(urlパラメータ)だけ差し替える。"""
    template = template.strip()
    if template.startswith("//"):
        template = "https:" + template
    base, _, query = template.partition("?")
    encoded = urllib.parse.quote(target_url, safe="")
    if not query:
        return f"{base}?url={encoded}"
    params = []
    replaced = False
    for pair in query.split("&"):
        key, _, _value = pair.partition("=")
        if key == "url":
            params.append(f"url={encoded}")
            replaced = True
        else:
            params.append(pair)
    if not replaced:
        params.append(f"url={encoded}")
    return base + "?" + "&".join(params)


def replace_affiliate_placeholders(body: str, settings: dict) -> str:
    affiliate = settings.get("affiliate") or {}
    amazon_tag = (affiliate.get("amazon_tag") or "").strip()
    moshimo_amazon = (affiliate.get("moshimo_amazon_link") or "").strip()
    moshimo_rakuten = (affiliate.get("moshimo_rakuten_link") or "").strip()

    def amazon_link(m: re.Match) -> str:
        keyword = m.group(1).strip()
        url = f"https://www.amazon.co.jp/s?k={urllib.parse.quote(keyword)}"
        if moshimo_amazon:
            # もしも経由(どこでもリンク)で成果計測する
            url = moshimo_wrap(url, moshimo_amazon)
        elif amazon_tag:
            url += f"&tag={urllib.parse.quote(amazon_tag)}"
        return f"👉 [Amazonで「{keyword}」を見る]({url})"

    def rakuten_link(m: re.Match) -> str:
        keyword = m.group(1).strip()
        url = f"https://search.rakuten.co.jp/search/mall/{urllib.parse.quote(keyword)}/"
        if moshimo_rakuten:
            url = moshimo_wrap(url, moshimo_rakuten)
        return f"👉 [楽天市場で「{keyword}」を探す]({url})"

    body = re.sub(r"\{\{AMAZON:([^}]+)\}\}", amazon_link, body)
    body = re.sub(r"\{\{RAKUTEN:([^}]+)\}\}", rakuten_link, body)
    return body


def escape_pipes_in_link_text(body: str) -> str:
    """リンクのアンカーテキスト内の | をエスケープする。

    記事タイトルには区切りの「|」が含まれる(例: AI動画編集ツール比較|...)。
    これを内部リンクのテキストにそのまま使うと、GitHub Pages(kramdown+GFM)が
    その行を表として誤認し、リンクが壊れて生のURLが本文に露出してしまう。
    アンカーテキスト内の未エスケープの | を \\| に変換して表化を防ぐ。
    """
    def repl(m: re.Match) -> str:
        text = re.sub(r"(?<!\\)\|", r"\\|", m.group(1))
        return f"[{text}]("

    return re.sub(r"\[([^\]\n]*)\]\(", repl, body)


def sanitize_slug(slug: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    return slug or fallback


def render_post(article: dict, settings: dict, date: datetime.date) -> str:
    body = replace_affiliate_placeholders(article["body"].strip(), settings)
    body = escape_pipes_in_link_text(body)
    tags = json.dumps(article["tags"], ensure_ascii=False)
    title = article["title"].replace('"', "'")
    description = article["description"].replace('"', "'")
    category = article.get("category") or CATEGORY_NAMES[0]
    if category not in CATEGORY_NAMES:
        category = CATEGORY_NAMES[0]
    return f"""---
layout: post
title: "{title}"
description: "{description}"
date: {date.isoformat()}
category: {category}
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


def save_article(article: dict, settings: dict) -> Path:
    """記事をJekyllの投稿ファイルとして保存し、パスを返す。"""
    today = datetime.datetime.now(JST).date()
    slug = sanitize_slug(article["slug"], fallback=f"post-{today.strftime('%Y%m%d')}")
    post_path = POSTS_DIR / f"{today.isoformat()}-{slug}.md"
    n = 2
    while post_path.exists():
        post_path = POSTS_DIR / f"{today.isoformat()}-{slug}-{n}.md"
        n += 1

    POSTS_DIR.mkdir(exist_ok=True)
    post_path.write_text(render_post(article, settings, today), encoding="utf-8")
    print(f"記事を保存しました: {post_path.relative_to(ROOT)}")
    return post_path


def generate_one(client, model: str, settings: dict, topics: dict, posts_meta: list[dict]) -> str | None:
    """トピックキューから記事を1本生成して保存し、タイトルを返す。トピックが無ければNone。"""
    pending = topics.get("pending") or []
    if not pending:
        return None
    topic = pending[0]
    print(f"トピック: {topic}")

    article = call_claude(client, model, build_article_prompt(topic, settings, posts_meta), ARTICLE_SCHEMA)
    save_article(article, settings)

    # 使い終わったトピックを done に移動し、途中で失敗しても整合するよう都度保存する
    topics["pending"] = pending[1:]
    topics.setdefault("done", []).append(topic)
    save_yaml(TOPICS_PATH, topics)
    return article["title"]


NEWS_RESEARCH_PROMPT = """あなたは日本語のAIツール・ガジェットブログの編集者です。
Web検索を使って、直近1週間のAI関連ニュース(新しいAIモデル・サービス・機能・ツールの発表など)を調べてください。

その中から、日本の一般読者にとって最も価値があり、ブログ記事として面白い話題を1つ選び、
記事化に必要な情報を箇条書きでまとめてください:

- 何が起きたか(いつ、誰が、何を発表したか)
- 読者にとって何が嬉しいのか・どう影響するのか
- 重要な事実・数字(価格、提供開始日、対象ユーザーなど)
- 出典URL(公式発表と報道記事を2〜3件)

検索で確認できた事実だけをまとめ、推測で補わないでください。"""


def build_news_article_prompt(research: str, settings: dict, posts_meta: list[dict]) -> str:
    article = settings["article"]
    links = internal_links_block(posts_meta)
    return f"""あなたは日本語のAIツール・ガジェット紹介ブログのライターです。
以下の調査メモをもとに、最新AIニュースの解説記事を1本書いてください。

# 調査メモ(事実はこの範囲内のみ使用し、推測で補わないこと)
{research}

# 文体・構成の指示
{article['tone']}
- 文字数は{article['min_chars']}〜{article['max_chars']}字程度。
- 「何が起きたか」→「読者にとって何が嬉しいか」→「使い方・注意点」→「まとめ」の流れで構成する。
- 不確かな点は断定せず「公式サイトで最新情報を確認してください」と促す。
- 記事の最後に「参考リンク」セクションを置き、調査メモの出典URLをMarkdownリンクで載せる。

# SEOの指示
- このニュースを検索する人が使いそうなキーワードを、タイトル・最初の見出し・冒頭段落に自然に含める。
- 冒頭の1〜2段落で「この記事を読むと何がわかるか」を明示する。

# 図解の挿入
内容の理解を助ける場合のみ、Mermaid記法(```mermaid、graph TDまたはLR、ノード8個以内、
ラベルは記号を含まない短い日本語、ノードIDは英数字)の図解を1つ入れてください。

{INTERNAL_LINK_INSTRUCTION.format(links=links)}"""


def research_news(client, model: str) -> str:
    """Web検索ツールで直近のAIニュースを調査し、記事の元になるメモを返す。"""
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": NEWS_RESEARCH_PROMPT}]
    for _ in range(5):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )
        # サーバー側ツールの反復上限に達した場合は続きを再要求する
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        return "\n".join(b.text for b in response.content if b.type == "text")
    raise RuntimeError("ニュース調査が規定回数内に完了しませんでした")


def generate_news(client, model: str, settings: dict, posts_meta: list[dict]) -> str | None:
    """週1回のニュース解説記事を生成する。失敗したらNoneを返す(通常記事にフォールバック)。"""
    try:
        print("今週のAIニュースを調査しています...")
        research = research_news(client, model)
        article = call_claude(
            client, model, build_news_article_prompt(research, settings, posts_meta), ARTICLE_SCHEMA
        )
        save_article(article, settings)
        return article["title"]
    except Exception as e:  # ニュース記事の失敗で毎日の投稿を止めない
        print(f"ニュース記事の生成に失敗したため通常記事に切り替えます: {e}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="ブログ記事を自動生成します")
    parser.add_argument("--dry-run", action="store_true", help="APIを呼ばずプロンプトだけ表示する")
    parser.add_argument("--extra", type=int, default=0, help="1日の上限と関係なく追加でN本生成する")
    args = parser.parse_args()

    settings = load_yaml(SETTINGS_PATH)
    topics = load_yaml(TOPICS_PATH)
    model = settings["model"]
    posts_per_day = int(settings.get("posts_per_day", 1))
    titles = existing_post_titles()
    posts_meta = existing_posts_meta()

    print(f"モデル: {model} / 1日の投稿本数: {posts_per_day}")

    if args.dry_run:
        pending = topics.get("pending") or []
        if not pending:
            print("エラー: トピックが空です。config/topics.yml の pending にトピックを追加してください。", file=sys.stderr)
            return 1
        print("\n===== 記事生成プロンプト(dry-run、1本目のみ表示) =====\n")
        print(build_article_prompt(pending[0], settings, posts_meta))
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

    if args.extra > 0:
        # 追加生成モード: その日の本数制限を無視して指定本数を生成する
        remaining = args.extra
        is_news_day = False
        print(f"追加生成モード: {remaining}本を生成します。")
    else:
        # 二重実行ガード: 遅延したcronと手動実行が重なっても、その日の本数を超えて投稿しない
        today_str = datetime.datetime.now(JST).date().isoformat()
        posted_today = len(list(POSTS_DIR.glob(f"{today_str}-*.md"))) if POSTS_DIR.exists() else 0
        remaining = posts_per_day - posted_today
        if remaining <= 0:
            print(f"本日分({posts_per_day}本)は投稿済みのためスキップします。")
            return 0
        if posted_today > 0:
            print(f"本日すでに{posted_today}本投稿済みのため、残り{remaining}本を生成します。")

        news_cfg = settings.get("news") or {}
        is_news_day = (
            news_cfg.get("enabled")
            and datetime.datetime.now(JST).weekday() == int(news_cfg.get("weekday", 0))
            and posted_today == 0  # その日の最初の実行のみニュース記事を生成
        )

    for i in range(remaining):
        print(f"--- {i + 1}/{remaining} 本目 ---")

        # ニュース解説の日は1本目をWeb検索付きのニュース記事にする
        if i == 0 and is_news_day:
            title = generate_news(client, model, settings, posts_meta)
            if title is not None:
                titles.append(title)
                posts_meta = existing_posts_meta()  # 新記事を内部リンク候補に反映
                continue
            # 失敗時はそのまま通常記事にフォールバック

        refill_topics_if_needed(client, model, settings, topics, titles, dry_run=False)
        title = generate_one(client, model, settings, topics, posts_meta)
        if title is None:
            print("エラー: トピックが空です。config/topics.yml の pending にトピックを追加してください。", file=sys.stderr)
            return 1
        titles.append(title)
        posts_meta = existing_posts_meta()  # 新記事を内部リンク候補に反映
    return 0


if __name__ == "__main__":
    sys.exit(main())
