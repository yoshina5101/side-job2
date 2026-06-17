"""既存記事の鮮度を更新する補助スクリプト。

古くなりやすい情報(料金・モデル名・最新動向など)をClaudeで最新化し、
front matter に `last_modified_at`(最終更新日)を付与する。
構成・内部リンク・アフィリエイトリンク・表・Mermaidはそのまま保持する。

使い方:
  python scripts/refresh_post.py --oldest 10        # 古い順に10件を一覧表示
  python scripts/refresh_post.py --post 2026-06-09-ai-chat-comparison.md --dry-run
  python scripts/refresh_post.py --post 2026-06-09-ai-chat-comparison.md
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_post as g  # noqa: E402

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)

REFRESH_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string", "description": "最新化した記事本文(Markdown)"},
        "changed": {"type": "boolean", "description": "実質的な更新があればtrue"},
        "summary": {"type": "string", "description": "更新点の短い説明(日本語)"},
    },
    "required": ["body", "changed", "summary"],
}


def split_front_matter(text: str) -> tuple[str, str]:
    m = FRONT_RE.match(text)
    if not m:
        raise ValueError("front matter が見つかりません")
    return m.group(1), m.group(2)


def set_last_modified(front: str, date_str: str) -> str:
    """front matter の last_modified_at を設定/更新する。"""
    if re.search(r"^last_modified_at:.*$", front, re.MULTILINE):
        return re.sub(r"^last_modified_at:.*$", f"last_modified_at: {date_str}", front, flags=re.MULTILINE)
    # date行の直後に挿入(無ければ末尾)
    if re.search(r"^date:.*$", front, re.MULTILINE):
        return re.sub(r"^(date:.*)$", rf"\1\nlast_modified_at: {date_str}", front, count=1, flags=re.MULTILINE)
    return front + f"\nlast_modified_at: {date_str}"


def build_prompt(title: str, body: str) -> str:
    return f"""あなたは日本語のAI・ガジェットブログの編集者です。次の既存記事を「最新化(リフレッシュ)」してください。

# 記事タイトル
{title}

# 既存の本文(Markdown)
{body}

# 最新化のルール(厳守)
- **古くなりやすい情報だけ**を最新の事実に更新する(例: 料金、モデル名・バージョン、「2026年X月時点」などの時点表現、すでに発売/終了した製品の状況)。
- 不確かな点は断定せず「公式サイトで最新情報を確認」と促す。事実を捏造しない。
- **構成・見出し・トーンは原則維持**。大幅な書き換えはしない。
- **以下は絶対にそのまま残す(改変・削除しない)**:
  - 内部リンク(例: `(/2026/06/09/xxx/)`)のURL
  - アフィリエイトリンク(`af.moshimo.com` を含むURL、`👉` の行)
  - 表(Markdownテーブル)・Mermaid(```mermaid ブロック)・画像
  - 「※本記事はアフィリエイト広告(PR)を含む…」の一文
- リンクのアンカーテキストに含まれる `|` はそのまま(エスケープ不要、こちらで処理する)。
- 出力の body は front matter を含めない、本文Markdownのみ。
- 実質的な変更が不要なら changed=false にして body は原文のまま返す。"""


def list_oldest(n: int) -> None:
    posts = sorted(g.POSTS_DIR.glob("*.md"))
    print(f"古い順 {min(n, len(posts))} 件(全{len(posts)}件):\n")
    for p in posts[:n]:
        text = p.read_text(encoding="utf-8")
        front, _ = split_front_matter(text)
        lm = re.search(r"^last_modified_at:\s*(.+)$", front, re.MULTILINE)
        title_m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", front, re.MULTILINE)
        title = title_m.group(1) if title_m else p.name
        mark = f"(更新済 {lm.group(1).strip()})" if lm else "(未更新)"
        print(f"  {p.name}  {mark}\n      {title}")


def resolve_post(name: str) -> Path:
    p = Path(name)
    if not p.is_absolute():
        p = g.POSTS_DIR / p.name
    if not p.exists():
        raise SystemExit(f"記事が見つかりません: {p}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="既存記事を最新化し最終更新日を付与する")
    ap.add_argument("--post", help="対象記事のファイル名(_posts内)またはパス")
    ap.add_argument("--oldest", type=int, help="古い順にN件を一覧表示して終了")
    ap.add_argument("--dry-run", action="store_true", help="APIを呼ばず対象とプロンプトのみ表示")
    args = ap.parse_args()

    if args.oldest:
        list_oldest(args.oldest)
        return 0

    if not args.post:
        ap.error("--post か --oldest のどちらかを指定してください")

    path = resolve_post(args.post)
    settings = g.load_yaml(g.SETTINGS_PATH)
    model = settings["model"]
    text = path.read_text(encoding="utf-8")
    front, body = split_front_matter(text)
    title_m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", front, re.MULTILINE)
    title = title_m.group(1) if title_m else path.stem

    if args.dry_run:
        print(f"対象: {path.name}\nモデル: {model}\n")
        print("===== 最新化プロンプト(dry-run) =====\n")
        print(build_prompt(title, body))
        return 0

    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: ANTHROPIC_API_KEY が設定されていません。", file=sys.stderr)
        return 1

    import anthropic

    client = anthropic.Anthropic()
    print(f"最新化中: {path.name} (model={model}) ...")
    result = g.call_claude(client, model, build_prompt(title, body), REFRESH_SCHEMA, max_tokens=16000)

    today = datetime.datetime.now(g.JST).date().isoformat()
    new_body = g.escape_pipes_in_link_text(result["body"].strip())

    if not result.get("changed"):
        print(f"実質的な更新なし: {result.get('summary', '')}")
        print("最終更新日のみ付与します。")
        new_body = g.escape_pipes_in_link_text(body.strip())

    new_front = set_last_modified(front, today)
    path.write_text(f"---\n{new_front}\n---\n\n{new_body}\n", encoding="utf-8")
    print(f"完了: {path.name}")
    print(f"  最終更新日: {today}")
    print(f"  更新点: {result.get('summary', '(なし)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
