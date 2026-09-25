# CLAUDE.md

このファイルは、本リポジトリ（BaseLine）で Claude Code が作業する際のガイドです。

## プロジェクト概要

**BaseLine** は、新NISA「攻撃フェーズ」で保有する投資信託（現在はTracers/SOX/S&P500/オルカンの4銘柄がTier通知対象。FANG+は新規購入停止で表示のみ）について、あらかじめ決めた「暴落対応ルール（階層型Tierトリガー）」を毎朝自動で監視し、Tier到達時にLINEへ通知する個人用ツールです。

- **背景・目的**: 従来は人手で毎日基準価額を確認し下落率を計算していたが、見逃しリスクと運用負荷をゼロにするため自動化した。詳細な要件は [暴落監視ダッシュボード_要件定義書.md](暴落監視ダッシュボード_要件定義書.md) を参照。
- **開発の経緯**: 元々 Google AntiGravity で構築されたプロジェクトを、本セッションから Claude Code に開発移管した。
- **投資判断の自動化は行わない**。買付注文は常にSBI証券での手動操作。本ツールは「気づき」を与えるだけ。

## 対象銘柄とTier閾値（設定来高値比の下落率）

| 銘柄ID | 名称 | Tier1 | Tier2 | Tier3 |
|---|---|---|---|---|
| `tracers` | Tracers S&P500トップ10インデックス（米国株式） | -12% | -20% | -28% |
| `fang` | iFreeNEXT FANG+インデックス（**2026-09以降 新規購入停止**。`active:false`でTier通知・LINE日次サマリー対象外、表示のみ継続） | -15% | -25% | -35% |
| `sox` | ニッセイSOX指数インデックスファンド | -10% | -18% | -28% |
| `sp500` | eMAXIS Slim米国株式（S&P500） | -7% | -12% | -18% |
| `orkan` | eMAXIS Slim全世界株式（オール・カントリー） | -6% | -10% | -15% |

これらの値・投入予定額・監視期間はすべて [config/settings.json](config/settings.json) で管理されており、**コード変更なしで調整可能**であることが非機能要件（NF-06）。閾値やロジックを変更する際は、まず settings.json を疑うこと。

## 処理フロー（毎朝 平日 JST 07:00、GitHub Actions）

`scripts/monitor.py` がエントリポイント。以下の順で実行される。

1. `config/settings.json` 読み込み + `sync_github_workflow()` で settings.json の `schedule` を `.github/workflows/monitor.yml` の cron 式に自動反映（JST→UTC変換）
2. `fetch_nav.py`: `settings.json`の`funds`全銘柄について日経新聞の投信ページ（`https://www.nikkei.com/nkd/fund/?fcode=<fund_code>`）を BeautifulSoup でスクレイピングし基準価額を取得。失敗時は `retry_count` 回まで `retry_interval_sec` 秒間隔でリトライ
2.5. `market_data.py`（市場心理指標）・`positions.py`（保有ポジション）を取得。Tier判定・通知に影響しない独立ステップで、失敗しても処理を継続（前回値を維持）
3. 取得失敗銘柄があれば `notify_fetch_error()` でLINE通知（F-11。`active:false`の銘柄は対象外）
4. `judge.py`: `data/peak.json`（設定来高値）を更新、下落率・Tier・基準日比・購入判定（BUY/WAIT/HOLD/HIGH）を計算
5. 新規Tier到達（`data/triggered.json` に未記録のTier）があれば `notify_tier_reached()` でLINE通知（重複通知防止のため一度発動したTierは再通知しない）。`active:false`（新規購入停止）の銘柄は通知・記録・回復判定を行わない（判定は`INACTIVE`）
6. 通知対象銘柄（`active:false`を除く）と保有ポジションの日次サマリーを `notify_daily_summary()` でLINE通知
7. `data/history.csv`（新銘柄の列は自動追加）/ `data/peak.json` / `data/triggered.json` を保存（`market.json`・`positions.json`・`positions_history.csv`はそれぞれの取得ステップで保存）
8. `generate_dashboard.py`: `public/index.html` を生成（Chart.jsはCDN読み込み）。購入実績（`purchase_history.json`）から平均取得単価・グラフマーカーも描画
9. GitHub Actions が `history.csv`/`peak.json`/`triggered.json`/`market.json`/`positions.json`/`positions_history.csv` と `public/index.html`、更新された `monitor.yml` をコミット・プッシュし、GitHub Pages にデプロイ（`purchase_history.json`は運用者が手動コミットするため対象外）

## ファイル構成

```
config/settings.json    # 銘柄・Tier閾値・原資金額・監視期間・スケジュール（唯一の設定源）
data/history.csv         # 日次基準価額の蓄積（追記のみ、同日は上書きしない＝べき等）
data/peak.json            # 銘柄ごとの設定来高値（peak_start_date以降の最高値。銘柄別の基準日はbaseline.datesで指定）
data/triggered.json      # 発動済みTier記録（重複通知防止のstate）
data/purchase_history.json  # 購入実績（約定日・単価・区分・金額。運用者が手動追記。平均取得単価・グラフマーカーの元データ）
data/market.json / positions.json / positions_history.csv  # 市場心理・保有ポジションの最新値と履歴（自動更新）
scripts/fetch_nav.py     # 日経新聞スクレイピング
scripts/market_data.py   # 市場心理指標（VIX/米10年金利/USD-JPY）
scripts/positions.py     # 保有ポジション（Tier対象外）の取得・含み損益判定
scripts/purchase_history.py  # 購入実績の読み込み・平均取得単価の算出（表示専用。判定・通知には使わない）。実績は口座区分account(attack/side)・units(口数)を任意で持つ
scripts/portfolio.py     # 長期ポートフォリオ（攻撃フェーズ＋別枠積立）の統合ビュー・サテライト比率（表示専用の純粋関数。設定はsettings.jsonのlong_term_portfolio）
scripts/import_sbi_history.py  # SBI証券の約定履歴CSVをpurchase_history.jsonへ取り込む（手動実行。口座は預り区分、攻撃フェーズの区分は日付から自動推定。CSVはコミットしない）
scripts/update_purchases.py    # 上記の取り込み→purchase_history.jsonのみコミット→pull --rebase→プッシュを確認つきで1コマンド実行（手動実行）
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
- **銘柄の追加・停止は設定のみで行う**: `settings.json`の`funds`に追加、新規購入停止は`"active": false`。`history.csv`は列が自動で増える。銘柄IDをコードに固定しない（`.get(fund_id, [])`で未登録を空扱いする）。
- **`--dry-run`は通知送信コードパスを通らない**（8/10-11の本番クラッシュの教訓、要件定義書 残課題No.7）。通知関連の変更は`_send_line_message`をモックして`notify_*`を実際に呼んで確認すること。また`--dry-run`もデータファイル（`data/*`）を書き換えるため、テスト後は`git checkout`で戻す（本番の記録はGitHub Actionsに一本化）。
- **別枠積立（クレジットカード払いの毎月積立。オルカン30,000円=つみたて投資枠、Tracers20,000円=成長投資枠）は別会計**: 預り区分だけでは判別できないため取り込みは`sbi_import.side_fixed_amounts`（固定金額）で判定する。`purchase_history.json`の`account:"side"`で記録し、平均取得単価カード・チャートマーカー（攻撃フェーズ=`attack`、省略時）には含めない。合算は`portfolio.py`（統合ビュー）だけが行う。口座区分は`attack`（攻撃フェーズ）/`side`（別枠積立）/`legacy`（旧つみたてNISA。保持方針）。統合ビューは保有全ファンドの評価額合計を「全体」とし、グループ`role`（`core`/`satellite`）のサテライト比率と、`share_review`（S&P500比率の維持判断ルール。基準は未設定=null）を表示する。NASDAQ100・SBI・Vの基準価額は`positions.items`の`hidden:true`で取得。はじめてのNISAは子供用の別枠のため統合ビューに含めない。`positions.items`の`hidden:true`は「基準価額の取得・保存のみ行い、カード/チャートタブ/LINEには出さない」（SBI・V・S&P500）。
- **公開リポジトリ**: SBI証券の約定履歴CSV等の個人の取引明細はコミットしない。
- **スコープ外**: 自動発注、高度な予測AI、複数ユーザー対応・ログイン機能、SOXの出口判定（売却判定）の自動化は要件定義で明示的に対象外。

## コーディング規約（既存コードに準拠）

- 日本語のモジュール/関数docstringが標準（英語コメントに置き換えない）
- Python型ヒントを使用（`float | None` などPython 3.10+構文）
- ログは `logging` モジュール経由、`print` は標準出力向けのテスト用スクリプトのみ
- `scripts/__pycache__/*.pyc` がリポジトリにコミットされている（`.gitignore` が存在しないため）。新規に `.gitignore` を追加する場合は、既存の追跡ファイルの扱いについて必ずユーザーに確認すること。
