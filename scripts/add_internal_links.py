#!/usr/bin/env python3
"""既存の記事に内部リンクを後付けで挿入するスクリプト(1回限りの運用ツール)。

本文の文章は一切書き換えず、「すでに本文にある語句」をリンクで包むだけの安全方式。
AIには (anchor=本文中の既存の語句, url=リンク先) のペアだけを返させ、
挿入はPython側で行う。

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/add_internal_links.py            # 全記事に適用
    python scripts/add_internal_links.py --dry-run  # 変更内容だけ表示(書き込まない)
"""

import argparse
import sys

import generate_post as g

INSERTION_SCHEMA = {
    "type": "object",
    "properties": {
        "insertions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchor": {"type": "string", "description": "本文中に既に存在する連続した語句(完全一致)。見出しや表の中は避ける"},
                    "url": {"type": "string", "description": "リンク先URL(候補リストのいずれか)"},
                },
                "required": ["anchor", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insertions"],
    "additionalProperties": False,
}


def split_front_matter(text: str) -> tuple[str, str]:
    """(front_matter込みヘッダ, 本文) に分割する。"""
    parts = text.split("---\n", 2)
    if len(parts) == 3 and parts[0] == "":
        return "---\n" + parts[1] + "---\n", parts[2]
    return "", text


def build_prompt(title: str, body: str, candidates: list[dict]) -> str:
    links = "\n".join(f"- [{c['title']}]({c['url']})" for c in candidates)
    return f"""次のブログ記事に、関連する「サイト内の他記事」への内部リンクを2〜3個提案してください。

# ルール(厳守)
- anchor は記事本文に**すでに存在する語句を完全一致で**指定する(言い換え・新しい文の追加は禁止)。
- anchor は本文の通常の段落から選ぶ。見出し・表・コードブロック・既存リンクの中は避ける。
- url は下の候補リストにあるものだけを使う。関連性の高いものだけを選び、無理に数を増やさない。
- この記事自身へのリンクは含めない。

# 記事タイトル
{title}

# 記事本文
{body}

# サイト内の他記事(リンク先候補)
{links}
"""


def insert_links(body: str, insertions: list[dict], allowed_urls: set[str]) -> tuple[str, list[str]]:
    """本文中の既存語句をリンクで包む。安全のため、見出し・コード・既存リンク行は対象外。"""
    lines = body.split("\n")
    used_urls: set[str] = set()
    applied: list[str] = []
    for ins in insertions:
        anchor = (ins.get("anchor") or "").strip()
        url = (ins.get("url") or "").strip()
        if not anchor or url not in allowed_urls or url in used_urls:
            continue
        in_fence = False
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or stripped.startswith("#") or stripped.startswith("|"):
                continue
            if "](" in line or "👉" in line:  # 既存リンク/アフィリンク行は触らない
                continue
            pos = line.find(anchor)
            if pos == -1:
                continue
            lines[idx] = line[:pos] + f"[{anchor}]({url})" + line[pos + len(anchor):]
            used_urls.add(url)
            applied.append(f"{anchor} → {url}")
            break
    return "\n".join(lines), applied


def main() -> int:
    parser = argparse.ArgumentParser(description="既存記事に内部リンクを後付けする")
    parser.add_argument("--dry-run", action="store_true", help="変更内容を表示するだけで書き込まない")
    args = parser.parse_args()

    posts_meta = g.existing_posts_meta()
    settings = g.load_yaml(g.SETTINGS_PATH)
    model = settings["model"]

    import os
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: ANTHROPIC_API_KEY が未設定です。", file=sys.stderr)
        return 1
    client = None
    if not args.dry_run:
        import anthropic
        client = anthropic.Anthropic()

    total = 0
    for post in sorted(g.POSTS_DIR.glob("*.md")):
        url = g.post_url_from_filename(post.name)
        meta = next((p for p in posts_meta if p["url"] == url), None)
        if not meta:
            continue
        candidates = [p for p in posts_meta if p["url"] != url]
        if not candidates:
            continue

        text = post.read_text(encoding="utf-8")
        header, body = split_front_matter(text)
        if "](/20" in body:  # 既に内部リンクがある記事はスキップ
            print(f"skip(既にリンクあり): {post.name}")
            continue

        if args.dry_run:
            print(f"[dry-run] 対象: {post.name}(候補 {len(candidates)} 件)")
            continue

        result = g.call_claude(client, model, build_prompt(meta["title"], body, candidates), INSERTION_SCHEMA, max_tokens=2048)
        allowed = {c["url"] for c in candidates}
        new_body, applied = insert_links(body, result.get("insertions", []), allowed)
        if applied:
            post.write_text(header + new_body, encoding="utf-8")
            total += len(applied)
            print(f"{post.name}: {len(applied)}個挿入")
            for a in applied:
                print(f"    {a}")
        else:
            print(f"{post.name}: 挿入なし")

    print(f"\n合計 {total} 個の内部リンクを挿入しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
