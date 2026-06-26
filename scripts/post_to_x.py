#!/usr/bin/env python3
"""新しく公開した記事をXに自動投稿するスクリプト。

スレッド形式で投稿する:
  1本目: フック文 + タイトルカード画像(自動生成)
  2本目: 記事の要点(リプ)
  3本目: ブログURL(リプ)
本文にリンクを入れると表示が抑制されるため、リンクは最後のツイートに回す。

GitHub Actions から、記事生成・コミットの後に実行される想定。

    python scripts/post_to_x.py            # その日の新規記事を投稿
    python scripts/post_to_x.py --dry-run  # 投稿せずスレッド内容と画像生成を確認
"""

import argparse
import datetime
import os
import re
import sys
import tempfile
import textwrap
from pathlib import Path

import generate_post as g

X_POSTED_PATH = g.ROOT / "config" / "x_posted.yml"

# 日本語フォントの候補(GitHub Actionsでは fonts-ipafont-gothic を入れる)
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
]

THREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {
            "type": "string",
            "description": "スレッド1本目。フック型の本文。100字以内。URL・ハッシュタグなし。続きを読みたくなる書き出しにする。",
        },
        "body": {
            "type": "string",
            "description": "スレッド2本目。記事の要点を2〜3個、箇条書き(・)でまとめる。120字以内。URLなし。",
        },
    },
    "required": ["hook", "body"],
    "additionalProperties": False,
}


def site_url() -> str:
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


def find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# 行頭に置きたくない文字(句読点・閉じ括弧・記号)。前の行末にぶら下げる。
_NO_LINE_START = set("、。，．・:;：；?!?！)）]｝」』》〕】｜|ー〜")


def _char_wrap(draw, text: str, font, max_width: float) -> list[str]:
    """文字幅ベースで折り返す。英数字・モデル名(GPT-5.6等)は途中で割らない。"""
    import re

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*|.", text)
    lines: list[str] = []
    cur = ""
    for t in tokens:
        trial = cur + t
        if not cur or draw.textlength(trial, font=font) <= max_width:
            cur = trial
        elif t in _NO_LINE_START:
            cur += t  # 行頭禁則: 前の行末にぶら下げる
        else:
            lines.append(cur.rstrip())
            cur = "" if t == " " else t
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _wrap_segment(draw, text: str, font, max_width: float) -> list[str]:
    """1行ぶんのテキストを、スペース→文字単位の順で折り返す。"""
    lines: list[str] = []
    cur = ""
    for seg in text.split(" "):
        cand = f"{cur} {seg}".strip() if cur else seg
        if not cur or draw.textlength(cand, font=font) <= max_width:
            cur = cand
        else:
            lines.append(cur)
            cur = seg
    if cur:
        lines.append(cur)

    result: list[str] = []
    for ln in lines:
        if draw.textlength(ln, font=font) <= max_width:
            result.append(ln)
        else:
            result.extend(_char_wrap(draw, ln, font, max_width))
    return result


def _wrap_title(draw, title: str, font, max_width: float) -> list[str]:
    """改行文字(\\n)は強制改行として尊重し、各行をさらに幅で折り返す。"""
    result: list[str] = []
    for hard_line in title.split("\n"):
        if hard_line == "":
            continue
        result.extend(_wrap_segment(draw, hard_line, font, max_width))
    return result


def _fit_title(draw, font_path: str, title: str, max_width: float, max_lines: int = 4):
    """最大行数に収まる最大のフォントサイズを選び、(font, lines)を返す。"""
    from PIL import ImageFont

    for size in (58, 52, 46, 42, 38):
        font = ImageFont.truetype(font_path, size)
        lines = _wrap_title(draw, title, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
    font = ImageFont.truetype(font_path, 38)
    return font, _wrap_title(draw, title, font, max_width)[:max_lines]


def make_title_card(title: str, category: str) -> Path | None:
    """記事タイトル入りのタイトルカード画像を生成する。フォントが無ければNone。"""
    font_path = find_font()
    if not font_path:
        return None
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 675
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    c1, c2 = (12, 22, 32), (17, 34, 46)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    cyan, teal = (47, 214, 234), (10, 126, 140)
    d.rectangle([0, 0, W, 7], fill=cyan)
    d.rectangle([0, H - 7, W, H], fill=teal)

    # ロゴマーク(左上)
    lx, ly, ls = 70, 60, 92
    d.rounded_rectangle([lx, ly, lx + ls, ly + ls], radius=20, outline=cyan, width=7)
    d.line([(lx + 24, ly + 28), (lx + 50, ly + 46), (lx + 24, ly + 64)], fill=cyan, width=7, joint="curve")
    d.line([(lx + 56, ly + 66), (lx + 74, ly + 66)], fill=(90, 184, 255), width=7)
    f_brand = ImageFont.truetype(font_path, 34)
    d.text((lx + ls + 20, ly + 26), "AIツール・ガジェットラボ", font=f_brand, fill=(174, 191, 201))

    # カテゴリーバッジ
    f_cat = ImageFont.truetype(font_path, 30)
    if category:
        cw = d.textlength(category, font=f_cat)
        d.rounded_rectangle([70, 200, 70 + cw + 36, 250], radius=10, fill=teal)
        d.text((88, 207), category, font=f_cat, fill=(255, 255, 255))

    # タイトル(意味の区切りを尊重し、文字幅でバランス良く折り返す)
    title_x = 70
    max_width = W - title_x - 70  # 左右マージン
    f_title, lines = _fit_title(d, font_path, title, max_width, max_lines=4)
    line_h = f_title.size + 20
    y = 300
    for line in lines:
        d.text((title_x, y), line, font=f_title, fill=(242, 247, 249))
        y += line_h

    out = Path(tempfile.gettempdir()) / "x_card.png"
    img.save(out, optimize=True)
    return out


def build_thread(client, model: str, title: str, description: str) -> dict:
    prompt = f"""日本語のAIツール・ガジェットブログの新着記事を告知するXスレッド(2投稿分)を作ってください。

# 記事タイトル
{title}

# 記事の概要
{description}

# 条件
- 1本目(hook): 続きを読みたくなるフック。100字以内。URL・ハッシュタグなし。
- 2本目(body): 記事の要点を2〜3個、箇条書き(・)で。120字以内。URLなし。
- 煽りすぎず、初心者にやさしいトーン。絵文字は控えめに。"""
    return g.call_claude(client, model, prompt, THREAD_SCHEMA, max_tokens=1024)


def post_thread(hook: str, body: str, url: str, image_path: Path | None) -> None:
    """スレッド(フック+画像 → 要点 → リンク)を投稿する。"""
    import tweepy

    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    api_v1 = tweepy.API(auth)
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )

    media_ids = None
    if image_path and image_path.exists():
        media = api_v1.media_upload(filename=str(image_path))
        media_ids = [media.media_id]

    t1 = client.create_tweet(text=hook, media_ids=media_ids)
    id1 = t1.data["id"]
    t2 = client.create_tweet(text=body, in_reply_to_tweet_id=id1)
    id2 = t2.data["id"]
    client.create_tweet(text=f"詳しくはこちら👇\n{url}", in_reply_to_tweet_id=id2)


def has_credentials() -> bool:
    return all(
        os.environ.get(k)
        for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="新着記事をXにスレッド投稿します")
    parser.add_argument("--dry-run", action="store_true", help="投稿せず内容と画像生成を確認する")
    args = parser.parse_args()

    settings = g.load_yaml(g.SETTINGS_PATH)
    x_cfg = settings.get("x") or {}
    if not x_cfg.get("enabled"):
        print("X自動投稿は無効です(config/settings.yml の x.enabled が false)。")
        return 0

    model = settings["model"]
    base = site_url()

    state = g.load_yaml(X_POSTED_PATH) if X_POSTED_PATH.exists() else {}
    posted = state.get("posted") or []

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
        category = fm.get("category", "")
        url = base + g.post_url_from_filename(post.name)
        try:
            image_path = None
            try:
                image_path = make_title_card(title, category)
            except Exception as e:  # 画像生成の失敗は致命的にしない(画像なしで続行)
                print(f"画像生成に失敗(画像なしで続行): {e}", file=sys.stderr)

            if args.dry_run:
                print(f"--- {post.name} ---")
                print(f"画像: {'生成OK ' + str(image_path) if image_path else 'なし(フォント未検出)'}")
                print(f"URL: {url}\n")
                continue

            thread = build_thread(client, model, title, description)
            post_thread(thread["hook"], thread["body"], url, image_path)
            posted.append(post.name)
            state["posted"] = posted
            g.save_yaml(X_POSTED_PATH, state)
            print(f"投稿しました: {post.name}\n  {thread['hook']}")
        except Exception as e:
            print(f"投稿に失敗しました({post.name}): {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
