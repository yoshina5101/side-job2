#!/usr/bin/env python3
"""手動投稿用に、記事のXスレッド文とタイトルカード画像を生成するヘルパー。

X APIは投稿に有料プランが必要なため、自動投稿ではなく「下書き」を作る。
生成したツイート文はジョブ概要(GITHUB_STEP_SUMMARY)と標準出力に、
画像は x_drafts/ に保存する(GitHub ActionsではArtifactとしてダウンロード可能)。

    python scripts/draft_x.py --count 1   # 最新1記事の下書きを作る
"""

import argparse
import os
import shutil
import sys

import generate_post as g
import post_to_x as px

OUT_DIR = g.ROOT / "x_drafts"


def main() -> int:
    parser = argparse.ArgumentParser(description="X投稿の下書き(文+画像)を作成します")
    parser.add_argument("--count", type=int, default=1, help="最新から何記事分の下書きを作るか")
    args = parser.parse_args()

    settings = g.load_yaml(g.SETTINGS_PATH)
    model = settings["model"]
    base = px.site_url()

    posts = sorted(g.POSTS_DIR.glob("*.md"), reverse=True)[: max(1, args.count)]
    if not posts:
        print("記事がありません。", file=sys.stderr)
        return 1

    import anthropic
    client = anthropic.Anthropic()

    OUT_DIR.mkdir(exist_ok=True)
    blocks = []
    for post in posts:
        fm = px.parse_front_matter(post.read_text(encoding="utf-8"))
        title = fm.get("title", "")
        desc = fm.get("description", "")
        cat = fm.get("category", "")
        url = base + g.post_url_from_filename(post.name)

        thread = px.build_thread(client, model, title, desc)
        img_name = f"{post.stem}.png"
        try:
            img = px.make_title_card(title, cat)
            if img:
                shutil.copy(img, OUT_DIR / img_name)
        except Exception as e:
            print(f"画像生成に失敗(画像なし): {e}", file=sys.stderr)
            img_name = "(画像生成に失敗)"

        block = f"""## {title}

**① 1本目(この画像を添付 → `{img_name}`)**

```
{thread['hook']}
```

**② 2本目(①へのリプ)**

```
{thread['body']}
```

**③ 3本目(②へのリプ)**

```
詳しくはこちら👇
{url}
```
"""
        blocks.append(block)
        print(block)

    md = "\n---\n".join(blocks)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("# 📝 X投稿の下書き\n\n")
            f.write("下の文章をコピペし、画像は **Artifacts の x-drafts** からダウンロードして、"
                    "Xアプリで「①(画像付き)→ ②リプ → ③リプ」の順に手動投稿してください。\n\n")
            f.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
