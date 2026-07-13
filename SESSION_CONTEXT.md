# SESSION_CONTEXT.md

BaseLineプロジェクトにおける、Claude Codeとの作業セッションの引き継ぎ記録です。
新しいセッションを開始する際はこのファイルの最新セクションを確認し、作業終了時には内容を追記してください。

## 使い方

- 各セッションの作業終了時に、下部に新しいエントリを追記する（古いエントリは削除しない）
- 「現在の状態」セクションは常に最新の状態に更新する（ここだけ読めば現状把握できるようにする）
- 些細な作業（typo修正等）は記録不要。設計判断・仕様変更・未解決の課題を優先的に記録する

---

## 現在の状態（最終更新: 2026-07-13）

- **開発移管**: Google AntiGravityで構築されたプロジェクトを、本セッションからClaude Codeでの開発に移管。[CLAUDE.md](CLAUDE.md) を新規作成し、プロジェクト理解・開発ガイドとして整備した。
- **稼働状況**: GitHub Actionsによる平日朝7:00 JST自動実行が稼働中。データ取得元は日経電子版（要件定義書記載のYahoo!ファイナンスから変更済み、ドキュメントも追従済み）。2026-07-13に本番実行（`workflow_dispatch`, dry_run=false）でLINE通知・ダッシュボード反映まで動作確認済み（コミット `24295dc`）。
- **既知の不具合はレビューで発見した3件をすべて修正済み**（下記コミット参照）：
  1. `date.today()`がUTC基準で日付・曜日判定を行い、JST実行時刻との間で日付が1日ずれる不具合
  2. `schedule.daily`が`true`のままで土日も実行され、休場日のNAVが別日付で重複記録される不具合
  3. 監視開始前（`peak_start_date`未到達）に設定来高値が仮登録され、NAVがそれを上回ると下落率が負値になりLINE通知が「--1.1%」のような二重マイナス表記になる不具合（`-0.0%`表記も同根で修正）
- **peak_start_date変更（2026-07-13）**: `config/settings.json`の`peak_start_date`を`2026-08-01`（②期間開始日）から`2026-07-07`（購入判定基準日`baseline.date`と同一日）に変更。理由は要件定義書に「②期間開始日と意図的に一致」と明記されていたが、ユーザーが「特に理由がなければ今月から記録したい」と希望したため、baseline.dateに揃える方針を選択。この変更に伴い、**ピーク追跡開始（`peak_start_date`）と実際の資金投入期間（②期間開始日）が分離**された点に注意：②期間開始前でもTier到達・LINE通知が発生し得る。あわせて`peak_start_date`を過去日付に設定した際の取りこぼし防止として、`history.csv`の記録済み履歴を遡って最高値を確認する処理（`judge.seed_initial_peak()`）を追加し、`update_peak()`に統合した。
- **今回スコープ外とした既知の設計上の懸念**（ユーザー合意の上で現状維持）：
  - 購入判定（BUY/WAIT）の基準日価格（`settings.json`の`baseline`）が固定値で自動更新されないため、長期的な相場上昇局面ではTier到達時にもWAIT判定が続く可能性がある。詳細は[暴落監視ダッシュボード_要件定義書.md](暴落監視ダッシュボード_要件定義書.md)の残課題No.5を参照。
  - 祝日（土日以外の休場日）は自動スキップ対象外。土日のみ対応。
- **直近の設計変更（コミット履歴より）**:
  - 投資判断ロジック（BUY/WAIT/HOLD/HIGH）と基準日比表示を導入（`judge.judge_decision()`）
  - `settings.json` の `schedule` から `monitor.yml` のcron式を自動同期する仕組みを追加（`monitor.sync_github_workflow()`、JST/UTC曜日ズレも補正済み）
  - LINE通知に日次サマリー通知を追加（Tier到達時通知とは別枠。README上も「Tier到達時のみ」ではなく実態に合わせて記載済み）
  - `.gitignore`整備・`__pycache__`のリポジトリからの除外、判定表示（絵文字・ラベル・色）の`judge.decision_display()`への一元化

---

## セッション履歴

### 2026-07-13: Claude Codeへの開発移管
- リポジトリ全体（README、要件定義書、config、scripts、workflow）を読み込み、BaseLineの目的・仕様を把握
- [CLAUDE.md](CLAUDE.md) を新規作成（プロジェクト概要、Tier構造、処理フロー、ファイル構成、開発コマンド、注意点、コーディング規約）
- 本ファイル（SESSION_CONTEXT.md）を新規作成し、以後のセッション引き継ぎの仕組みを整備
- コード変更・機能追加は未実施（ドキュメント整備のみ）

### 2026-07-13: コード品質レビューと不具合修正
- `gh run list`・コミット履歴の突き合わせにより、本番環境で実際に発生していた3件の不具合（日付タイムゾーンずれ、土日重複記録、下落率マイナス二重表示）を発見。ユーザーとBUY/WAIT基準日ロジックの扱い・git同期方針を確認の上、優先順位順に修正を実施
- 修正内容: [scripts/monitor.py](scripts/monitor.py)のJST日付化・土日スキップガード、cronのUTC/JST曜日補正、[scripts/judge.py](scripts/judge.py)の`calc_drawdown`クランプとピーク初期シードの`peak_start_date`ガード、[README.md](README.md)の通知説明修正、ダッシュボードのHIGH配色・データ出典修正、判定表示ロジックの`judge.decision_display()`への一元化、`.gitignore`整備
- ローカル`--dry-run`実行・土日スキップのシミュレーション確認を経てコミット（`24295dc`）・push
- ユーザー指示によりGitHub Actionsを`workflow_dispatch`（dry_run=false）で本番実行し、LINE通知送信成功・GitHub Pagesダッシュボード反映まで確認（実行ID `29239746022`）
- 本ドキュメント一式（README.md / SESSION_CONTEXT.md / 要件定義書）を今回の修正内容に合わせて最新化
- ユーザーから「history.csv/peak.jsonが更新されていないように見える」との質問。history.csvは事前のローカルdry-run実行で既に本日分が記録済みだったための重複防止スキップ、peak.jsonは`peak_start_date`（当時2026-08-01）未到達のため空のままという、いずれも意図した挙動であることを確認・説明
- `peak_start_date`設定の理由（②期間開始日と意図的に一致）をユーザーに説明した上で、「baseline.date（2026-07-07）に揃える」方針で変更することに合意。[scripts/judge.py](scripts/judge.py)に`seed_initial_peak()`を追加し`update_peak()`に統合（過去日付への変更に伴う履歴遡り確認・取りこぼし防止）、[scripts/monitor.py](scripts/monitor.py)の重複していた初期シード処理を整理・簡素化、ダッシュボードガイド内の固定文言「2026年8月以降」を動的表示に修正。ローカル`--dry-run`で過去日優先ロジック・HIGH判定表示を確認済み（コミット・push未実施）
