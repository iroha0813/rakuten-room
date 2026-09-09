# 楽天ROOM 投稿候補の自動生成

毎朝、楽天の公式APIから商品を集めてスコアリングし、Claude で紹介文を書いて、
スマホで開ける候補ページを自動生成する。**ROOMへの投稿だけ人がやる**半自動構成。

```
[GitHub Actions 毎朝07:00 JST]
  収集 → 選定 → 紹介文生成 → 候補ページ公開 → 履歴コミット
        ↓
[あなた 5分]  ページを開く → 紹介文をコピー → ROOMで「コレ！」
        ↓
[あなた 週1〜月1]  成果CSVをDL → 分析 → スコア重みを調整
```

## なぜ投稿を自動化しないのか

楽天ROOMの規約はプログラムによる自動操作を禁じている。**凍結されれば収益はゼロ**になるので、
アカウントを危険に晒す価値はない。一方で商品選定・文章執筆・整形は楽天の公式API（楽天ウェブサービス）と
Claude APIで完全に自動化でき、これは規約上まったく問題ない。作業として残るのは「コピーして貼る」だけ。

なお、**ROOMは「コレ！」投稿した時点でアフィリエイトリンクを自動生成する**。
こちらでリンクを作って設置する工程は存在しないので、必要なのは楽天市場の商品URLだけ。

## 紹介文について

`room/write.py` のシステムプロンプトは、**実際に使った体験の創作を禁止している**。
購入していない商品に「届いてすぐ使ってます」と書けば、それは読み手を欺くことになるため。
レビューの傾向・価格・仕様といった与えられた事実だけを根拠に書かせている。

## セットアップ

### 1. 資格情報を用意する

| 環境変数 | 取得先 |
|---|---|
| `RAKUTEN_APP_ID` | [Rakuten Developers](https://webservice.rakuten.co.jp/) のアプリ管理画面 |
| `RAKUTEN_ACCESS_KEY` | 同上（**現行APIは applicationId だけでは動かない**） |
| `RAKUTEN_AFFILIATE_ID` | 楽天アフィリエイト（ROOM運用では未使用。将来のブログ展開用） |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |

### 2. ローカル環境

```powershell
uv venv --python 3.12 .venv
uv pip install --link-mode=copy --python .venv\Scripts\python.exe -r requirements-dev.txt
Copy-Item .env.example .env
```

> `--link-mode=copy` は OneDrive 配下でハードリンクが張れないため必要。

`.env` をテキストエディタで開き、`=` の右側に値を貼り付けて保存します。

```dotenv
RAKUTEN_APP_ID=f843e532-....
RAKUTEN_ACCESS_KEY=....
RAKUTEN_AFFILIATE_ID=
ANTHROPIC_API_KEY=sk-ant-....
```

`.env` は `.gitignore` されているので GitHub には上がりません。本物の環境変数が
設定されている場合はそちらが優先されるため、GitHub Actions では Secrets がそのまま効きます。

> **注意**: このプロジェクトは OneDrive 配下にあるため、`.env` も OneDrive に
> 同期されクラウド上に保存されます。それを避けたい場合はプロジェクトごと
> OneDrive の外（例: `C:\dev\rakuten-room`）に移してください。

### 3. 疎通確認とジャンルID確定

```powershell
.\.venv\Scripts\python.exe -m room.rakuten --smoke   # 4エンドポイントの疎通
.\.venv\Scripts\python.exe scripts\fetch_genres.py   # config/genres.yaml を生成
```

`fetch_genres.py` は `config/settings.yaml` の `genres.<key>.match` キーワードで
楽天のルートジャンルを引き当てる。意図と違うジャンルが選ばれたら `--list` で一覧を見て
`match` を直すか、`config/genres.yaml` を手で編集する。

### 4. GitHub

1. リポジトリを作成（**パブリック**。無料プランでは private の Pages が使えないため）
2. Settings → Secrets and variables → Actions に上記4つを登録
3. Settings → Pages → Source を `main` ブランチの `/docs` に設定
4. Actions タブから `daily-candidates` を `Run workflow` で手動実行して確認

秘密情報はすべて Secrets に置き、リポジトリには含めない。
成果CSVと収益分析結果は `local/` に置かれ `.gitignore` されるので公開されない。

> GitHub Pro を持っているなら private リポジトリのままでよい（コード変更は不要）。

## 使い方

```powershell
# 本番と同じ動作（履歴を更新する）
.\.venv\Scripts\python.exe -m room.pipeline

# お試し（LLMを呼ばず、履歴も更新しない）
.\.venv\Scripts\python.exe -m room.pipeline --local

# 収集の内訳だけ見る
.\.venv\Scripts\python.exe -m room.collect --dry-run

# LLMに投げるプロンプトを目視確認する（APIキーを使わない）
.\.venv\Scripts\python.exe -m room.write --dry-run

# ページのデザインだけ作り直す
.\.venv\Scripts\python.exe -m room.render data\candidates\2026-08-27.json

# 成果CSVを取り込んで分析する
.\.venv\Scripts\python.exe -m room.report import local\report_202608.csv

# テスト
.\.venv\Scripts\python.exe -m pytest
```

## 設定

コードに定数は埋め込まない。挙動を変えたいときは `config/` を編集する。

| ファイル | 内容 |
|---|---|
| `config/settings.yaml` | 生成件数・ジャンル配分・価格帯・レビュー基準・LLMモデル・CSV列名 |
| `config/genres.yaml` | 楽天ジャンルID（`scripts/fetch_genres.py` が生成） |
| `config/weights.yaml` | スコアリングの重み（`room/report.py` の分析を見て調整） |

よく触るもの:

- `daily_count` — 1日の候補件数。慣れてきたら増やす
- `genres.<key>.share` — ジャンルの配分比率
- `collect.min_review_average` / `min_review_count` — 候補が少なすぎるときに緩める
- `sale_boost` — お買い物マラソン期間に特定ジャンルを厚くする
- `llm.model` — `claude-opus-5` にすれば文章の質は上がる（コストも上がる）
- `product_type.categories` — 「マットレスばかり出る」のように同じ商品タイプが
  繰り返し投稿されているのに気づいたら、キーワードを追記する（`selection.max_per_type`
  で同日内の上限、`weights.yaml` の `type_repeat` で直近日数の減点が効く）

## 毎日の自動実行

Oracle Cloud の無料枠VPS（`rakuten-room-vps`、固定IP）上で cron が毎朝07:00 JSTに
`scripts/run_daily.sh` を実行し、候補生成からGitHubへのpushまで自動で行う。

以前はローカルPC + Windowsタスクスケジューラ（`run_daily.ps1`）で運用していたが、
家庭用回線のIPが変動して楽天APIの許可IPから頻繁に外れる問題（403 CLIENT_IP_NOT_ALLOWED）が
続いたため、固定IPを持つVPSでの実行に移行した（2026-09-09）。ローカルでの手動実行コマンド
（下記「使い方」）は引き続き使える。

VPS側のセットアップ内容:

- Python 3.9（Oracle Linux 9標準）+ venv、依存パッケージは`requirements.txt`と同じ
- `.env` はローカルからscpで転送（内容は本番と同一）
- GitHubへのpushは Deploy Key（read/write）+ SSH経由。パスワード認証や個人PATは使わない
- cronは `crontab -l` で確認できる。ログは `local/logs/cron.log` と日付別ログの両方に出る

## データ

| パス | 内容 |
|---|---|
| `data/posted.jsonl` | 提示済み商品の履歴。**重複投稿防止の正**。1行1件 |
| `data/candidates/YYYY-MM-DD.json` | その日の候補スナップショット |
| `docs/index.html` | 最新の候補ページ（GitHub Pages が配信） |
| `docs/archive/` | 過去7日分の候補ページ |
| `local/conversions.jsonl` | 取り込んだ成果データ（gitignore） |
| `local/analysis_*.json` | 分析結果（gitignore） |

SQLiteではなくJSONLを使っている。GitHub Actions が毎日コミットするため、
バイナリDBだとgit差分が読めずコンフリクトも解決できないため。

## コスト

Claude Haiku 4.5（$1 / $5 per MTok）で1日5件なら**月100円未満**。
楽天APIは無料。GitHub Actions もパブリックリポジトリなら無料枠内。

## 期待値

ROOMアカウントが未稼働からのスタートなので、**最初の1〜2ヶ月は収益ほぼゼロが正常**。
楽天アフィリエイトの料率は概ね2%で、5,000円の商品が売れて約100円。

このシステムの価値は即座に稼ぐことではなく、毎日の投稿を5分に圧縮して継続を可能にし、
売れ筋データを蓄積して選定精度を上げ続けること。`room/report.py` の分析が効き始めるのは
提示件数と成約件数が溜まる3ヶ月目以降。
