# CLAUDE.md

このファイルは、本リポジトリ（BaseLine）で Claude Code が作業する際のガイドです。

## プロジェクト概要

**BaseLine** は、新NISA「攻撃フェーズ」で保有する投資信託4銘柄について、あらかじめ決めた「暴落対応ルール（階層型Tierトリガー）」を毎朝自動で監視し、Tier到達時にLINEへ通知する個人用ツールです。

- **背景・目的**: 従来は人手で毎日基準価額を確認し下落率を計算していたが、見逃しリスクと運用負荷をゼロにするため自動化した。詳細な要件は [暴落監視ダッシュボード_要件定義書.md](暴落監視ダッシュボード_要件定義書.md) を参照。
- **開発の経緯**: 元々 Google AntiGravity で構築されたプロジェクトを、本セッションから Claude Code に開発移管した。
- **投資判断の自動化は行わない**。買付注文は常にSBI証券での手動操作。本ツールは「気づき」を与えるだけ。

## 対象銘柄とTier閾値（設定来高値比の下落率）

| 銘柄ID | 名称 | Tier1 | Tier2 | Tier3 |
|---|---|---|---|---|
| `fang` | iFreeNEXT FANG+インデックス | -15% | -25% | -35% |
| `sox` | ニッセイSOX指数インデックスファンド | -10% | -18% | -28% |
| `sp500` | eMAXIS Slim米国株式（S&P500） | -7% | -12% | -18% |
| `orkan` | eMAXIS Slim全世界株式（オール・カントリー） | -6% | -10% | -15% |

これらの値・投入予定額・監視期間はすべて [config/settings.json](config/settings.json) で管理されており、**コード変更なしで調整可能**であることが非機能要件（NF-06）。閾値やロジックを変更する際は、まず settings.json を疑うこと。

## 処理フロー（毎朝 平日 JST 07:00、GitHub Actions）

`scripts/monitor.py` がエントリポイント。以下の順で実行される。

1. `config/settings.json` 読み込み + `sync_github_workflow()` で settings.json の `schedule` を `.github/workflows/monitor.yml` の cron 式に自動反映（JST→UTC変換）
2. `fetch_nav.py`: 日経新聞の投信ページ（`https://www.nikkei.com/nkd/fund/?fcode=<fund_code>`）を BeautifulSoup でスクレイピングし基準価額を取得。失敗時は `retry_count` 回まで `retry_interval_sec` 秒間隔でリトライ
3. 取得失敗銘柄があれば `notify_fetch_error()` でLINE通知（F-11）
4. `judge.py`: `data/peak.json`（設定来高値）を更新、下落率・Tier・基準日比・購入判定（BUY/WAIT/HOLD/HIGH）を計算
5. 新規Tier到達（`data/triggered.json` に未記録のTier）があれば `notify_tier_reached()` でLINE通知（重複通知防止のため一度発動したTierは再通知しない）
6. 全銘柄の日次サマリーを `notify_daily_summary()` でLINE通知
7. `data/history.csv` / `data/peak.json` / `data/triggered.json` を保存
8. `generate_dashboard.py`: `public/index.html` を生成（Chart.jsはCDN読み込み）
9. GitHub Actions が `data/` と `public/index.html`、更新された `monitor.yml` をコミット・プッシュし、GitHub Pages にデプロイ

## ファイル構成

```
config/settings.json    # 銘柄・Tier閾値・原資金額・監視期間・スケジュール（唯一の設定源）
data/history.csv         # 日次基準価額の蓄積（追記のみ、同日は上書きしない＝べき等）
data/peak.json            # 銘柄ごとの設定来高値（2026-08-01以降の最高値）
data/triggered.json      # 発動済みTier記録（重複通知防止のstate）
scripts/fetch_nav.py     # 日経新聞スクレイピング
scripts/judge.py         # 下落率/Tier/期間/購入判定/トレンド計算ロジック
scripts/notify.py        # LINE Messaging API 通知（メッセージ生成 + 送信）
scripts/generate_dashboard.py  # public/index.html 生成
scripts/monitor.py       # 上記を束ねるオーケストレーター
public/index.html        # GitHub Pagesで公開される生成物（手で編集しない）
.github/workflows/monitor.yml  # 定時実行ワークフロー（cronはsettings.jsonから自動同期される）
```

## 期間（フェーズ）ロジック

投入予定資金は「②期間」（2026/08〜2027/02）と「③期間」（2027/03〜2027/08）の2フェーズに分かれ、未消化分は2027/12末まで延長可。`judge.detect_period()` が今日の日付から `before_start` / `phase2` / `phase3` / `extension` / `ended` / `none` を判定する。フェーズ判定を変更する場合はこの関数と `config/settings.json` の `periods` を両方確認すること。

## 開発・テストコマンド

```bash
pip install -r requirements.txt

# ダッシュボード生成のみ確認（LINE通知はスキップ）
python scripts/monitor.py --dry-run

# 基準価額取得のみ単体テスト
python scripts/fetch_nav.py

# 判定ロジックの単体テスト（judge.py内のテストコード）
python scripts/judge.py

# LINE通知のテスト送信（要環境変数）
$env:LINE_CHANNEL_ACCESS_TOKEN = "..."
$env:LINE_USER_ID = "..."
python scripts/notify.py --test
```

GitHub Actions 上では `workflow_dispatch` から `dry_run: true` で手動テスト実行も可能。

## 重要な注意点

- **スクレイピングの脆弱性**: `fetch_nav.py` は日経電子版のCSSセレクタ（`.m-stockPriceElm_value`）に依存している。サイト構造変更で取得失敗する可能性があるため、変更時は要件定義書の「残課題」も参照。要件定義書ではYahoo!ファイナンスを想定していたが、実装では日経電子版に変更されている点に注意（ドキュメントとコードの情報源が異なる）。
- **シークレット**: `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` はGitHub Secretsで管理。コードやconfigに直書きしない。
- **cronの自動同期**: `monitor.yml` の cron 式を直接編集しても、次回 `monitor.py` 実行時に `settings.json` の `schedule` の値で上書きされる。スケジュール変更は `settings.json` 側で行うこと。
- **triggered.json は重複通知防止のための唯一のstate**。誤って削除するとTier到達通知が再送されるため、消す場合は影響を理解した上で行う。
- **public/index.html は生成物**。手動編集しても次回実行で上書きされる。テンプレート変更は `generate_dashboard.py` を編集する。
- **スコープ外**: 自動発注、高度な予測AI、複数ユーザー対応・ログイン機能は要件定義で明示的に対象外。

## コーディング規約（既存コードに準拠）

- 日本語のモジュール/関数docstringが標準（英語コメントに置き換えない）
- Python型ヒントを使用（`float | None` などPython 3.10+構文）
- ログは `logging` モジュール経由、`print` は標準出力向けのテスト用スクリプトのみ
- `scripts/__pycache__/*.pyc` がリポジトリにコミットされている（`.gitignore` が存在しないため）。新規に `.gitignore` を追加する場合は、既存の追跡ファイルの扱いについて必ずユーザーに確認すること。
