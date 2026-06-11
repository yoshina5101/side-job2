# AIツール・ガジェットラボ — AI自動ブログ運営システム

毎朝7時に、AI(Claude)がブログ記事を1本自動生成して公開する「ほったらかし運用」のブログシステムです。収益はアフィリエイト(Amazon・楽天など)と、PVが増えてからのGoogle AdSenseで狙います。

## 仕組み

```
毎朝7時(日本時間)
  └─ GitHub Actions が起動
       └─ Claude API が記事を1本執筆(トピックは config/topics.yml から自動選択)
            └─ _posts/ に保存してコミット
                 └─ GitHub Pages がブログとして自動公開
```

- **ホスティング費用**: 無料(GitHub Pages)
- **自動実行費用**: 無料(GitHub Actions)
- **AI費用**: 1記事あたり約15〜20円 → 毎日投稿でも **月500〜600円程度**

## セットアップ手順(初回のみ・約15分)

### 1. Anthropic APIキーを取得する

1. [Anthropic Console](https://console.anthropic.com/) にアクセスしてアカウントを作成
2. クレジットカードを登録し、少額(5ドル程度)をチャージ
3. 「API Keys」から新しいキーを作成し、`sk-ant-...` で始まる文字列をコピー

### 2. GitHubにAPIキーを登録する

1. このリポジトリのページで **Settings → Secrets and variables → Actions** を開く
2. **New repository secret** をクリック
3. Name に `ANTHROPIC_API_KEY`、Secret にコピーしたAPIキーを貼り付けて保存

### 3. GitHub Pagesを有効にする

> ⚠️ GitHubの無料プランでは、**リポジトリをPublic(公開)にしないとGitHub Pagesが使えません**。Settings → General の一番下「Change visibility」から変更できます。

1. **Settings → Pages** を開く
2. 「Source」を **Deploy from a branch** にし、Branch を `main` / `(root)` に設定して保存
3. 数分後、`https://<ユーザー名>.github.io/<リポジトリ名>/` でブログが公開されます

### 4. 動作確認(手動で1記事生成してみる)

1. リポジトリの **Actions** タブを開く
2. 左側の「記事の自動生成」を選択 → **Run workflow** をクリック
3. 数分後、`_posts/` に新しい記事が追加され、ブログに反映されれば成功です

以降は毎朝7時に自動で記事が増えていきます。

## 収益化の手順

### アフィリエイト(最初にやること)

記事中の商品リンクは自動で挿入されますが、**報酬を受け取るにはASP(アフィリエイト事業者)への登録が必要**です。

| ASP | 内容 | 登録先 |
|---|---|---|
| Amazonアソシエイト | Amazon商品の紹介料(2〜10%) | https://affiliate.amazon.co.jp/ |
| 楽天アフィリエイト | 楽天市場の紹介料 | https://affiliate.rakuten.co.jp/ |
| もしもアフィリエイト | Amazon・楽天両方を扱える(審査が比較的やさしい) | https://af.moshimo.com/ |
| A8.net | AIツール系サービスの案件が豊富 | https://www.a8.net/ |

**Amazonアソシエイトに合格したら**、`config/settings.yml` の `amazon_tag` にトラッキングID(例: `yourname-22`)を設定してください。以降に生成される記事のAmazonリンクに自動で反映されます。

> 💡 Amazonアソシエイトの審査には一定の記事数とアクセスが必要です。まずは「もしもアフィリエイト」経由でAmazon・楽天を扱う方法が初心者にはおすすめです。

### Google AdSense(記事が増えてから)

記事が30本以上たまり、アクセスが安定してきたら [Google AdSense](https://adsense.google.com/) に申請しましょう。合格後、AdSenseの管理画面で取得した広告コードをサイトに追加します(その際はこのリポジトリでClaudeに「AdSenseコードを設置して」と頼めばOKです)。

## カスタマイズ

| やりたいこと | 編集するファイル |
|---|---|
| **1日の投稿本数を変える** | `config/settings.yml` の `posts_per_day`(コスト目安もファイル内に記載) |
| 記事のトピックを追加・変更する | `config/topics.yml` の `pending` に追記 |
| AIモデルを変更してコストを下げる | `config/settings.yml` の `model`(`claude-haiku-4-5` で約1/5のコスト) |
| 文体・文字数を変える | `config/settings.yml` の `article` |
| 投稿頻度を変える | `.github/workflows/generate-post.yml` の `cron`(例: 週3回なら `0 22 * * 1,3,5`) |
| サイト名・説明を変える | `_config.yml` |

トピックは残り5本を切ると、Claudeが既存記事と重複しない新トピックを自動で補充するので、基本的に放置で大丈夫です。

## ローカルでのテスト

```bash
pip install -r requirements.txt

# APIを呼ばずにプロンプトだけ確認
python scripts/generate_post.py --dry-run

# 実際に1記事生成(APIキーが必要)
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/generate_post.py
```

## SEO(検索エンジン対策)

技術的なSEO設定は組み込み済みです。

| 項目 | 状態 |
|---|---|
| meta description / OGP / 構造化データ(JSON-LD) | ✅ 自動出力(jekyll-seo-tag) |
| sitemap.xml | ✅ 自動生成(jekyll-sitemap) |
| RSSフィード(feed.xml) | ✅ 自動生成(jekyll-feed) |
| robots.txt | ✅ 設置済み |
| 記事タイトル・見出しへのキーワード配置 | ✅ 生成プロンプトで自動対応 |
| Google Search Console への登録 | ⚠️ **手動(下記参照)** |

### Google Search Console への登録(公開後に必ずやる)

検索結果に載るスピードが大きく変わるので、サイト公開後に必ず行ってください。

1. [Google Search Console](https://search.google.com/search-console) にアクセスし、「URLプレフィックス」でサイトURL(`https://yoshina5101.github.io/side-job2/`)を登録
2. 所有権の確認は「HTMLタグ」方式を選び、表示されたメタタグの `content` の値をコピー
3. `_config.yml` に次の1行を追加してコミット(jekyll-seo-tagが自動でタグを出力します)
   ```yaml
   google_site_verification: "コピーした値"
   ```
4. 確認が済んだら、Search Console の「サイトマップ」に `sitemap.xml` を送信

### 知っておいてほしいこと

- **独自ドメインの検討**: `xxx.github.io` のままでも運用できますが、長期的に育てるなら独自ドメイン(年1,500円程度)の方がSEO上の資産になります。導入する場合は `_config.yml` の `url` を変更してください。
- **AI生成コンテンツについて**: Googleは「AI生成かどうか」ではなく「読者に役立つか」で評価すると公表しています。本システムのプロンプトは検索意図への回答を重視した構成にしていますが、ときどき記事を読んで、間違いに気づいたら直す(または好調なテーマにトピックを寄せる)運用が検索評価を伸ばす近道です。

## 法律・税金に関する注意

- **ステマ規制(景品表示法)**: アフィリエイトリンクを含む記事には広告である旨の表記が義務付けられています。本システムは全記事の冒頭に「※本記事はアフィリエイト広告(PR)を含みます。」を自動挿入することで対応しています。
- **確定申告**: 給与所得者の場合、副業の所得(収入−経費)が**年間20万円を超えたら**確定申告が必要です。API利用料やドメイン代は経費にできます。
- **各ASPの規約**: Amazonアソシエイト等には独自の表示ルールがあります。登録時に規約を確認してください。

## 収益の目安(現実的な期待値)

ブログ収益は積み上げ型です。最初の3〜6ヶ月はほぼゼロが普通で、記事数とともに検索流入が増えていきます。

- 〜3ヶ月(記事 約90本): 月0〜数百円
- 6ヶ月〜(記事 約180本): 月数百〜数千円
- 1年〜(記事 約365本): 月数千円〜(内容・ジャンル次第で大きく変動)

運用コストが月数百円なので、低リスクで続けられるのがこのシステムの強みです。アクセス解析([Google Search Console](https://search.google.com/search-console) への登録がおすすめ)を見ながら、伸びているテーマのトピックを `config/topics.yml` に追加していくと成長が速くなります。
