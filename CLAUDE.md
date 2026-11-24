# CLAUDE.md

このファイルは、本リポジトリ（BaseLine）で Claude Code が作業する際のガイドです。

## プロジェクト概要

**BaseLine** は、新NISAで保有する全資産（投資信託8本・米国個別株2銘柄）の最高値比・平均取得単価比・Tier到達状況を毎朝自動で監視し、ダッシュボード（GitHub Pages）に表示して、積立対象の銘柄が新しいTierに到達したときにLINEへ「下落の気づき」を通知する個人用ツールです。

- **背景・目的**: 従来は人手で毎日基準価額を確認し下落率を計算していたが、見逃しリスクと運用負荷をゼロにするため自動化した。詳細な要件は [暴落監視ダッシュボード_要件定義書.md](暴落監視ダッシュボード_要件定義書.md) を参照。
- **開発の経緯**: 元々 Google AntiGravity で構築されたプロジェクトを、本セッションから Claude Code に開発移管した。2026-11-24に、旧計画（期間・Tier別投入額・BUY/WAIT判定）を廃止し、SBIの一次情報（約定履歴CSV）に基づく現状から再構成した。
- **投資判断の自動化は行わない**。買付注文は常にSBI証券での手動操作。本ツールは「気づき」を与えるだけで、LINEに載せるのは**最高値比と平均取得単価比だけ**（購入額・BUY表示・原資は出さない）。
- 計画策定・資産配分・NISA積立の相談では、最初に `Personal/README.md`（Git管理外）を読み、正本の場所に従う。

## 保有資産とTier閾値（設定来高値比の下落率）

| 資産ID | 名称 | 区分 | アラート | Tier1 | Tier2 | Tier3 |
|---|---|---|---|---|---|---|
| `sbi_v_sp500` | SBI・V・S&P500（旧NISA＋つみたて） | core | − | -11% | -17% | -33% |
| `sp500` | eMAXIS Slim米国株式（S&P500） | core | ○ | -11% | -17% | -33% |
| `orkan` | eMAXIS Slim全世界株式（オール・カントリー） | core | ○ | -11% | -17% | -33% |
| `tracers` | Tracers S&P500トップ10インデックス | satellite | ○ | -12% | -20% | -28% |
| `nasdaq100` | ニッセイNASDAQ100インデックスファンド | satellite | ○ | -12% | -25% | -31% |
| `sox` | ニッセイSOX指数インデックスファンド（新規買付停止） | satellite | − | -16% | -33% | -45% |
| `fang` | iFreeNEXT FANG+インデックス（新規買付停止） | satellite | − | -15% | -29% | -36% |
| `newnisa_orkan` | はじめてのNISA・全世界株式（子供用・別会計） | separate | − | -11% | -17% | -33% |
| `tesla` | テスラ（TSLA） | satellite | − | -31% | -49% | -67% |
| `nvda` | エヌビディア（NVDA） | satellite | − | -24% | -38% | -67% |

これらの値・積立設定・NISA枠などはすべて [config/settings.json](config/settings.json) で管理されており、**コード変更なしで調整可能**であることが非機能要件（NF-06）。閾値やロジックを変更する際は、まず settings.json を疑うこと。サテライト＝オルカンとS&P500以外（`role`）。しきい値は、過去の下落頻度（Tier1＝年1回程度・Tier2＝約3年に1回・Tier3＝約10年に1回）で校正した値（根拠: `Personal/02_判断記録/Tierしきい値_検討結果.md` の標準感度。通知しきい値の目盛りで、売買の推奨ではない）。履歴が短い資産（SBI・V・はじめてのNISA・TSLA・NVDA）は約1年後に再校正する。

## 処理フロー（毎朝 平日 JST 07:00、GitHub Actions）

`scripts/monitor.py` がエントリポイント。以下の順で実行される。

1. `config/settings.json` 読み込み + `sync_github_workflow()` で settings.json の `schedule` を `.github/workflows/monitor.yml` の cron 式に自動反映（JST→UTC変換）
2. `fetch_nav.py`: `funds`全銘柄について日経新聞の投信ページ（`https://www.nikkei.com/nkd/fund/?fcode=<fund_code>`）を BeautifulSoup でスクレイピングし基準価額を取得。失敗時は `retry_count` 回まで `retry_interval_sec` 秒間隔でリトライ
2.5. `market_data.py`（市場心理指標）・`positions.py`（米国株TSLA・NVDAの株価。Yahoo Finance チャートAPI）を取得。失敗しても処理を継続（米国株は前回値を維持）
3. 取得失敗の資産があれば `notify_fetch_error()` でLINE通知（F-11）
4. `judge.py`: `data/peak.json`（設定来高値）を資産ごとの起点日（`baseline.dates`。最初の購入日）以降で更新。履歴に無い期間は約定単価も参考に初期値を決める
5. 全資産の最高値比・前日比・Tier・平均取得単価比を計算。`alert: true` の資産が**通知済みより深いTier**に到達したら `notify_tier_reached()` でLINE通知し、`data/triggered.json`（`{資産ID: {"tier","date"}}`）に記録。下落率が「通知済みTierのしきい値 × `alert.rearm_ratio`（0.5）」より浅くなったら通知済みTierを下げる（再武装。しきい値付近の往復で通知が数倍に増えるのを防ぐ）
6. 全保有資産の日次サマリー（「最高比」「平均比」のみ。プラス緑・マイナス赤のFlexメッセージ。失敗時はテキストで再送）を `notify_daily_summary()` でLINE通知
7. `data/history.csv`（新銘柄の列は自動追加）/ `data/peak.json` / `data/triggered.json` を保存（`market.json`・`positions.json`・`positions_history.csv`はそれぞれの取得ステップで保存）
8. `generate_dashboard.py`: `public/index.html` を生成（Chart.jsはCDN読み込み）。構成は 注意バナー（Tier到達中・取得失敗）／固定ナビ／ルールと見方／運用手順／投資意思決定サマリー／銘柄毎の詳細（折りたたみ）／基準価額の推移とTier閾値／市場心理／資金投入管理状況／長期ポートフォリオ（表はスマホでも1銘柄1行）
9. GitHub Actions が `history.csv`/`peak.json`/`triggered.json`/`market.json`/`positions.json`/`positions_history.csv` と `public/index.html`、更新された `monitor.yml` をコミット・プッシュし、GitHub Pages にデプロイ（`purchase_history.json`・`funding_status.json`は運用者が約定履歴の取り込み時にコミットするため対象外）

## ファイル構成

```
config/settings.json    # 資産・Tier閾値・積立設定・NISA枠・取込設定（唯一の設定源）
data/history.csv         # 投資信託の日次基準価額の蓄積（追記のみ、同日は上書きしない＝べき等）
data/peak.json            # 資産ごとの設定来高値（起点日は baseline.dates、なければ peak_start_date）
data/triggered.json      # 通知済みTier（重複通知防止のstate）
data/purchase_history.json  # 購入実績（約定日・単価・口数・金額・区分・口座区分・NISA枠。約定履歴CSVから取り込む）
data/market.json / positions.json / positions_history.csv  # 市場心理・米国株の最新値と履歴（USD。usdjpy列つき。自動更新）
data/funding_status.json # 資金管理（原資の月数・使用率、NISA枠の使用率。割合・月数のみ。update_purchases.pyが更新）
scripts/assets.py        # 全保有資産（funds[]→positions.items）を統一して扱う（kind/role/alert/tiers/currency）
scripts/fetch_nav.py     # 日経新聞スクレイピング（投資信託）
scripts/market_data.py   # 市場心理指標（VIX/米10年金利/USD-JPY）
scripts/positions.py     # 米国株の株価取得（Yahoo Finance）・履歴
scripts/purchase_history.py  # 購入実績の読み込み・平均取得単価（円/USD）の算出（表示専用。通知の判断には使わない）
scripts/portfolio.py     # 長期ポートフォリオ（評価額・コア/サテライト比率）の統合ビュー（表示専用の純粋関数）
scripts/groups.py        # 保有を5つの枠（積立設定の原資枠／クレカ枠・保持枠・旧NISA・こども枠）にグルーピング。LINE・サマリー・ポートフォリオで共通（表示専用）
scripts/funding.py       # 資金管理（原資の月数・NISA枠使用率）。原資の総額は Personal/capital.json（Git管理外）
scripts/import_sbi_history.py  # SBI証券の約定履歴CSV（投信・米国株）をpurchase_history.jsonへ取り込む（手動実行。CSVはコミットしない）
scripts/update_purchases.py    # 取り込み→資金管理の更新→purchase_history.jsonとfunding_status.jsonのコミット→pull --rebase→プッシュを確認つきで1コマンド実行
scripts/migrate_purchase_history.py / migrate_data_layout.py  # 一度きりの移行（dry-run既定・--applyは承認後）
scripts/judge.py         # 下落率/Tier/通知済みTier/最高値更新/トレンド計算ロジック
scripts/notify.py        # LINE Messaging API 通知（メッセージ生成 + 送信）
scripts/generate_dashboard.py  # public/index.html 生成
scripts/monitor.py       # 上記を束ねるオーケストレーター
tests/                   # unittest（python -m unittest discover -s tests -v）
public/index.html        # GitHub Pagesで公開される生成物（手で編集しない）
.github/workflows/monitor.yml  # 定時実行ワークフロー（cronはsettings.jsonから自動同期される）
```

## 開発・テストコマンド

```bash
pip install -r requirements.txt

# 単体テスト
python -m unittest discover -s tests -v

# ダッシュボード生成のみ確認（LINE通知はスキップ）
python scripts/monitor.py --dry-run

# 基準価額取得のみ単体テスト
python scripts/fetch_nav.py

# 米国株の株価取得のみ単体テスト
python scripts/positions.py

# 判定ロジックの単体テスト（judge.py内のテストコード）
python scripts/judge.py

# LINE通知のテスト送信（要環境変数）
$env:LINE_CHANNEL_ACCESS_TOKEN = "..."
$env:LINE_USER_ID = "..."
python scripts/notify.py --test
```

GitHub Actions 上では `workflow_dispatch` から `dry_run: true` で手動テスト実行も可能。

## 重要な注意点

- **スクレイピング・外部APIの脆弱性**: `fetch_nav.py` は日経電子版のCSSセレクタ（`.m-stockPriceElm_value`）に依存している。サイト構造変更で取得失敗する可能性があるため、変更時は要件定義書の「残課題」も参照。米国株は Yahoo Finance（米国）のチャートAPI（`query1/query2.finance.yahoo.com/v8/finance/chart/<ticker>`）から取得する（Yahoo!ファイナンスJPのHTMLは2026-10以降、価格をJSで描画するため取得できない）。GitHub Actions のIPから取得できるかは本番で要確認（失敗時は前回値で継続しLINEに取得エラーを通知）。要件定義書ではYahoo!ファイナンスを想定していたが、投資信託は日経電子版に変更されている点に注意。
- **シークレット**: `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` はGitHub Secretsで管理。コードやconfigに直書きしない。
- **cronの自動同期**: `monitor.yml` の cron 式を直接編集しても、次回 `monitor.py` 実行時に `settings.json` の `schedule` の値で上書きされる。スケジュール変更は `settings.json` 側で行うこと。
- **triggered.json は重複通知防止のための唯一のstate**。誤って削除するとTier到達通知が再送されるため、消す場合は影響を理解した上で行う。旧形式（Tier別レコードのリスト）は `load_triggered()` が新形式へ自動移行する。
- **public/index.html は生成物**。手動編集しても次回実行で上書きされる。テンプレート変更は `generate_dashboard.py` を編集する。
- **資産の追加・停止は設定のみで行う**: 投資信託は`funds`、米国株は`positions.items`に追加（`role`・`alert`・`tiers`）。Tier未設定は`tiers: []`。表示順は`funds`→`positions.items`の並び順。`history.csv`・`positions_history.csv`は列が自動で増える。資産IDをコードに固定しない（`.get(asset_id, [])`で未登録を空扱いする）。
- **`--dry-run`は通知送信コードパスを通らない**（8/10-11の本番クラッシュの教訓、要件定義書 残課題No.7）。通知関連の変更は`_send_line_message`をモックして`notify_*`を実際に呼んで確認すること（`tests/test_notify_settings.py`）。また`--dry-run`もデータファイル（`data/*`）を書き換えるため、テスト後は`git checkout`で戻す（本番の記録はGitHub Actionsに一本化）。
- **購入実績の口座区分 `account`（資金源）**: `attack`＝原資から出した買付（現金積立・手動の追加買付・個別株）／`side`＝クレジットカードの積立／`legacy`＝旧つみたてNISA（平均取得単価に含めない）／`child`＝子供用資金（別会計。原資・比率から除外）。NISA枠は`frame`（`growth`/`tsumitate`）。カード積立か手動買付かは約定履歴CSVに列がないため、`monthly_schedule.card.phases`の銘柄・金額・枠の一致で判定する（同月に重なる場合は日付が最も遅い約定をカード積立とみなして警告）。積立設定を変更したら`monthly_schedule`も更新すること。
- **資金管理の公開範囲**: 公開ページには「積立が続けられる月数」「原資の使用率」「NISA枠の使用率」だけを出し、原資の総額は`Personal/capital.json`（Git管理外）に置く。`funding_status.json`は金額を含めない。
- **枠のグルーピング**（`groups.py`）: 所属は`monthly_schedule`（cash / card）の最新の設定から自動判定。複数の枠に設定がある銘柄は両方に出し、保有は資金源（attack→原資枠、side→クレカ枠）で分ける。旧NISA（legacy）は別の行。保持枠＝積立設定のない保有、こども枠＝`role=separate`。
- **平均取得単価・比率の定義**: 平均取得単価は旧NISA（`legacy`）を除く全口座。米国株はUSD基準（為替を含まない）。長期ポートフォリオはコア（オルカン・S&P500系）以外をサテライトとし、子供用は分母に含めず、目標・上限は置かない（表示のみ）。
- **公開リポジトリ**: SBI証券の約定履歴CSV等の個人の取引明細・原資の総額はコミットしない（`Personal/`は`.gitignore`で除外。ファイルはUTF-8で保存）。
- **スコープ外**: 自動発注、高度な予測AI、複数ユーザー対応・ログイン機能、売却判定の自動化は要件定義で明示的に対象外。

## コーディング規約（既存コードに準拠）

- 日本語のモジュール/関数docstringが標準（英語コメントに置き換えない）
- Python型ヒントを使用（`float | None` などPython 3.10+構文）
- ログは `logging` モジュール経由、`print` は標準出力向けのテスト用スクリプトのみ
- テストは標準ライブラリの `unittest`（`tests/`）。新しい機能にはテストを足す
