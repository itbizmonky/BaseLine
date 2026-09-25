# 暴落監視ダッシュボード

新NISA攻撃フェーズ 暴落対応ルール監視システム

## 概要

| 項目 | 内容 |
|---|---|
| 実行方式 | GitHub Actions 平日自動実行（JST 07:00。実際の着信はGitHub Actions側の遅延により前後する） |
| 公開URL | GitHub Pages（セットアップ後に確定） |
| 通知 | LINE Messaging API（Tier到達時アラート ＋ 平日毎朝の日次サマリー。新規購入停止のFANG+は通知対象外） |
| 費用 | 無料（GitHub無料枠 / LINE Messaging API フリープラン） |
| UIデザイン | Cyber-Neumorphic (立体凹凸シャドウとネオン発光による近未来UI) |

---

## 対象銘柄・Tier閾値

| 銘柄 | Tier1 | Tier2 | Tier3 |
|---|---|---|---|
| Tracers S&P500トップ10インデックス（米国株式） | -12% | -20% | -28% |
| ニッセイSOX指数インデックスファンド | -10% | -18% | -28% |
| eMAXIS Slim米国株式（S&P500） | -7% | -12% | -18% |
| eMAXIS Slim全世界株式（オール・カントリー） | -6% | -10% | -15% |
| iFreeNEXT FANG+インデックス（**2026-09以降 新規購入停止**） | -15% | -25% | -35% |

FANG+は`config/settings.json`で`"active": false`としており、Tier到達の通知・LINE日次サマリーの対象外です（既存保有分の評価把握のため、基準価額の取得とダッシュボード表示のみ継続）。Tier投資予定額（`funds_phase2`/`funds_phase3`）は新配分（Tracers37%／SOX25%／S&P500 20%／オルカン18%）に合わせて再計算しています。

---

## ダッシュボードの主な表示

- **投資意思決定サマリー / 銘柄カード**: 各銘柄の現在の基準価額・設定来高値・下落率・Tier到達状況・判定（BUY/WAIT/HOLD/HIGH。新規購入停止のFANG+は「新規購入停止」）
- **長期ポートフォリオ（保有ファンドの合算。ページ最下部）**: 保有している全ファンド（オルカン・S&P500・SBI・V・SOX・FANG+・Tracers・NASDAQ100。攻撃フェーズ／別枠積立／旧つみたてNISA。子供用の別枠「はじめてのNISA」とテスラは含めない）の評価額の合計を「全体」として、**サテライト比率**（＝(SOX＋FANG+＋Tracers＋NASDAQ100)÷全体。目標20%との対比バー付き）と**S&P500比率**（＝(SBI・V・S&P500＋eMAXIS Slim S&P500)÷全体。維持判断ルール）を、計算式と金額つきで表示。2028年月初のリバランス判断用
- **毎月の運用手順（サマリ。ページ上部の開閉式ガイド）**: 自動で行われること、毎月の作業（約定履歴CSVの取り込み）、Tier到達時の対応、毎月見る数字
- **平均取得単価と含み損益**: 約定実績から算出した銘柄別の平均取得単価と現在の基準価額の比較（含み益=緑／含み損=赤・乖離率）。SOXの2028年月初の出口判定に使用
- **基準価額の推移とTier閾値**: 銘柄タブ選択時に、Tier1/2/3の閾値（破線）と購入実績（星マーカー。ホバーで約定日・単価・区分・金額）を表示
- **市場心理（参考情報）**: VIX・米10年金利・USD/JPY（判定・通知には使用しない）
- **資金投入管理状況**: 各Tierの投入済み／残枠資金
- **保有ポジション（監視専用）**: はじめてのNISA全世界株式・テスラの含み損益（取得単価は購入実績の平均取得単価）

---

## ファイル構成

```
.
├── .github/
│   └── workflows/
│       └── monitor.yml         # GitHub Actions 定時実行ワークフロー
├── config/
│   └── settings.json           # 銘柄設定・Tier閾値・原資金額（ここを編集）
├── data/
│   ├── history.csv             # 日次基準価額履歴（自動蓄積。銘柄追加時は列が自動で増える）
│   ├── peak.json               # 設定来高値記録（自動更新）
│   ├── triggered.json          # 発動済みTier記録（重複通知防止）
│   ├── market.json             # 市場心理指標（VIX/米10年金利/USD-JPY）の最新値（自動更新）
│   ├── positions.json          # 保有ポジション（監視専用）の最新値（自動更新）
│   ├── positions_history.csv   # 保有ポジションの日次価格履歴（自動蓄積）
│   └── purchase_history.json   # 購入実績（約定日・約定単価・区分・金額。運用者が購入のたびに手動追記）
├── scripts/
│   ├── monitor.py              # メイン実行スクリプト
│   ├── fetch_nav.py            # 基準価額取得
│   ├── market_data.py          # 市場心理指標（VIX/米10年金利/USD-JPY）取得・判定
│   ├── positions.py            # 保有ポジション（Tier投資対象外）取得・含み損益判定
│   ├── purchase_history.py     # 購入実績（約定実績）の読み込み・平均取得単価の算出
│   ├── portfolio.py            # 長期ポートフォリオ（攻撃フェーズ＋別枠積立）の統合ビュー・サテライト比率の算出
│   ├── import_sbi_history.py   # SBI証券の約定履歴CSVを購入実績に取り込む（手動実行）
│   ├── judge.py                # 判定ロジック
│   ├── notify.py               # LINE通知
│   └── generate_dashboard.py   # HTMLダッシュボード生成
├── public/
│   └── index.html              # 生成されたダッシュボード（自動更新）
├── requirements.txt
└── README.md
```

---

## 初回セットアップ手順

### Step 1: リポジトリの設定

1. GitHubリポジトリの **Settings → Pages** を開く
2. **Source** を `GitHub Actions` に変更して保存

### Step 2: LINE Messaging API の設定

1. [LINE Developers](https://developers.line.biz/ja/) にLINEアカウントでログイン
2. **「プロバイダーを作成」** → 任意の名前（例: `NISA監視Bot`）
3. **「Messaging APIチャンネルを作成」**
4. チャンネル作成後、**「Messaging API設定」タブ** → 最下部の **「チャネルアクセストークン（長期）」を発行** → コピー
5. **「チャンネル基本設定」タブ** → **「あなたのユーザーID」** をコピー（`U` で始まる文字列）
6. 作成したBotを自分のLINEで **「友だち追加」**

### Step 3: GitHub Secrets の設定

GitHubリポジトリの **Settings → Secrets and variables → Actions** を開き、以下を追加：

| Secret名 | 値 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | Step 2 でコピーしたチャネルアクセストークン |
| `LINE_USER_ID` | Step 2 でコピーしたユーザーID |

> ⚠️ これらの値はコードに直接書かないでください。必ずSecretsで管理します。

### Step 4: 原資金額の設定

`config/settings.json` を開き、`funds_phase2` と `funds_phase3` の金額を実際の値に書き換えてください。

```json
"funds_phase2": {
  "total": 2000000,
  "tier_amounts": {
    "sox":   {"tier1": 200000, "tier2": 300000, "tier3": 400000},
    ...
  }
}
```

### Step 5: 動作確認

1. **ローカルでのテスト（通知スキップ）:**
   ```bash
   pip install -r requirements.txt
   python scripts/monitor.py --dry-run
   # → public/index.html が生成されることを確認
   # --dry-run は土日でも実行可能（本番実行時のみJST基準で土日を自動スキップします）
   ```

2. **LINE通知のテスト:**
   ```powershell
   $env:LINE_CHANNEL_ACCESS_TOKEN = "YOUR_TOKEN"
   $env:LINE_USER_ID = "YOUR_USER_ID"
   python scripts/notify.py --test
   # → 自分のLINEにテストメッセージが届くことを確認
   ```

3. **GitHub Actions の手動実行テスト:**
   - GitHubリポジトリ → **Actions** タブ → 「暴落監視ダッシュボード 日次実行」
   - **「Run workflow」** ボタン → `dry-run: true` で実行
   - ログを確認し、成功したらGitHub PagesのURLでダッシュボードを確認

---

## 通常運用

- **毎朝JST 07:00**（平日のみ）に自動実行（GitHub Actions側の負荷状況により、実際の実行・通知は30〜60分程度遅れることがある）
- 手動で最新状態を確認したい場合は Actions → Run workflow（dry_run: false）
- 平日毎朝、LINEに日次サマリーが届く（Tier到達時はさらにアラート通知も届く。新規購入停止のFANG+は含まれない）
- 購入したら、下記「購入実績の記録方法」に従って`data/purchase_history.json`に1件追記する（平均取得単価・グラフのマーカーに反映される）
- ダッシュボードURLはブックマーク登録推奨

---

## 購入実績の記録方法

買付（SBI証券での約定）のたびに、`data/purchase_history.json`の該当銘柄のリストに1件追加してコミット・プッシュします（次回のダッシュボード生成時に、平均取得単価とグラフのマーカーへ自動反映されます）。

```json
{
  "sox": [
    {"date": "2026-07-07", "price": 50163, "category": "①分", "amount": 177683},
    {"date": "2026-08-03", "price": 42266, "category": "Tier1", "amount": 33316}
  ]
}
```

| 項目 | 内容 |
|---|---|
| キー（銘柄ID） | `tracers` / `sox` / `sp500` / `orkan` / `fang`、別枠積立のSBI・V・S&P500は`sbi_v_sp500`、保有ポジションは`newnisa_orkan`等（`config/settings.json`の`id`） |
| `date` | 約定日（SBI証券の約定履歴の約定日。例: `"2026-09-25"`） |
| `price` | 約定単価（1万口あたりの基準価額）。未確認の場合は`null`（平均取得単価の計算から除外され、ダッシュボードに警告が出る） |
| `category` | `Tier1`/`Tier2`/`Tier3`のほか`①分`・`定期積立`・`つみたて枠`等の自由な文言（マーカーはTier区分で色分け: Tier1=黄／Tier2=橙／Tier3=赤／その他=青） |
| `amount` | 投入金額（受渡金額、円） |
| `account`（任意） | `attack`（攻撃フェーズ＝成長投資枠。省略時）／`side`（別枠積立＝つみたて投資枠）。別枠積立は別会計で、平均取得単価カード・チャートのマーカーには含まれず、「長期ポートフォリオ」の合算にだけ使われます |
| `units`（任意） | 保有口数。省略時は`投入金額÷約定単価×10,000`で算出。SBI約定履歴の約定数量や、既存残高のように単価ではなく口数と取得総額が分かる場合に指定します |

- 平均取得単価 ＝ Σ投入金額 ÷ Σ購入口数 × 10,000（購入口数 ＝ 投入金額 ÷ 約定単価 × 10,000）
- 保有ポジション（`newnisa_orkan`等）に実績を登録すると、含み損益の基準（取得単価）が平均取得単価になります
- 実績が1件もない銘柄は、ダッシュボードに「データなし」と表示されます
- Tierに到達したが投資を見送った場合は購入実績を追加せず、`triggered.json`を`judge.set_investment_status()`で「見送り」に補正します

### 別枠積立（つみたて投資枠）の記録

別枠積立（クレジットカード払いの毎月積立。オルカン30,000円＝つみたて投資枠、Tracers20,000円＝成長投資枠。毎月9日発注）は`"account": "side"`で、攻撃フェーズと同じファンドでも別会計として記録します。

```json
{
  "orkan": [
    {"date": "2026-06-30", "category": "既存残高", "amount": 500000, "units": 130000, "account": "side"},
    {"date": "2026-09-10", "price": 37174, "category": "別枠積立", "amount": 10000, "account": "side"}
  ],
  "sbi_v_sp500": [
    {"date": "2026-07-10", "price": 41066, "category": "別枠積立", "amount": 10000, "account": "side"}
  ]
}
```

- **既存残高**（記録開始前に買った分）は、保有口数`units`と取得総額`amount`を1件の`既存残高`として登録します（SBI証券の保有残高画面のつみたて投資枠分から転記）。登録後は`config/settings.json`の`long_term_portfolio.provisional_note`を削除してください（暫定値の警告が消えます）
- SBI・V・S&P500は別ファンドのため、キー`sbi_v_sp500`に記録し、基準価額は`positions.items`（`hidden: true`）から自動取得されます（S&P500合計評価額の計算用。保有ポジション欄やLINEには出ません）
- **【推奨】SBI証券の約定履歴CSVから、取り込み〜コミット・プッシュまで1コマンドで行う**（CSVはコミットしないこと。手作業は月1回、CSVのダウンロードだけです）:
  ```bash
  python scripts/update_purchases.py 約定履歴.csv
  ```
  1. 取り込み予定（銘柄・約定日・単価・金額・口座・区分）を表示 → 確認（y/N）
  2. `data/purchase_history.json`へ書き込み、攻撃フェーズ分の平均取得単価を表示
  3. `purchase_history.json`だけをコミット → 確認（y/N）→ リモートの日次更新を取り込んでプッシュ

  `--yes`で確認を省略、`--no-push`でコミットまで。約定単価が確定した後（積立日の数日後）に実行してください。重複は自動でスキップされるので、期間が重なるCSVでも何度でも実行できます。
  - 口座は預り区分から自動判定します（NISA(つみたて)→別枠積立`side`、NISA(成長)→攻撃フェーズ`attack`）。ただし**別枠積立はクレジットカード払い**で、Tracersの別枠積立（月20,000円）は預り区分が成長投資枠です（CSVに決済方法の列がない）。そのため`config/settings.json`の`sbi_import.side_fixed_amounts`（銘柄ごとの別枠積立の固定金額。オルカン30,000円・Tracers20,000円）に一致する約定は別枠積立とみなします。**積立金額を変更したらここも更新してください**
  - 攻撃フェーズの区分は日付から自動推定します（初回購入日2026-07-07→「①分」、毎月の積立の約定日（23〜27日）→「定期積立」、それ以外→「未分類」。規則は`config/settings.json`の`sbi_import.attack_category_rules`）。**Tier1〜3の約定は自動判定できない**ため、グラフのマーカーをTier色にしたい場合だけ`purchase_history.json`の`category`を手動で書き換えてください（平均取得単価の計算には影響しません）
  - 取り込みだけ行う場合: `python scripts/import_sbi_history.py 約定履歴.csv [--include-attack] [--dry-run]`（既定は別枠積立のみ）。銘柄名と銘柄IDの対応は`config/settings.json`の`sbi_import`で設定します

---

## カスタマイズ

すべての設定は `config/settings.json` で変更可能：

- 銘柄の追加・削除
- Tier閾値の変更
- 原資金額の変更
- 監視期間の変更
- 実行時刻・平日/毎日の切り替え（`schedule.hour_jst` / `schedule.minute_jst` / `schedule.daily`）
  - `daily: false` の場合、JST平日（月〜金）のみ実行されます
  - 変更内容は次回の `monitor.py` 実行時に `.github/workflows/monitor.yml` のcron設定へ自動反映されます（手動でcronを編集する必要はありません）
- 市場心理指標の取得元URL・VIXのしきい値（`market_indicators.sources` / `market_indicators.vix_thresholds`）
  - `market_indicators.enabled: false` にすると市場心理セクション自体を無効化できます
- 保有ポジション（監視専用）の銘柄・取得単価・含み損益の警戒しきい値（`positions.items` / `positions.gain_loss_thresholds`）
  - `positions.enabled: false` にすると保有ポジションセクション自体を無効化できます
  - `items`の`source`に`nikkei_fund`（日経電子版の投資信託）または`yahoo_us_stock`（Yahoo!ファイナンス米国株、現状テスラ専用）を指定して取得元を切り替えます
  - 購入実績は`data/purchase_history.json`に、銘柄IDのキー（例: `"newnisa_orkan"`）で`{"date","price","category","amount"}`を追記します。実績がある保有ポジションは、取得単価が複数回購入の平均取得単価（`settings.json`の`cost_basis`より優先）になり、カードに取得日・投入金額合計・購入回数、推移チャート（該当銘柄のタブ選択時のみ）に購入ごとの星マーカーが表示されます。約定実績がない銘柄に限り、`items`の`purchase_date`・`purchase_amount`（任意）で1件だけ指定することもできます
- 監視銘柄の追加・停止（`funds`）: 新規銘柄は`funds`に1エントリ追加すると基準価額の取得・履歴（`history.csv`は列が自動追加される）・ダッシュボードに反映されます。新規購入を停止する銘柄は`"active": false`を指定します（Tier通知・LINE日次サマリーの対象外になり、ダッシュボードでは「（新規購入停止・監視終了）」表示のまま基準価額の表示のみ継続）
- 銘柄別の平均取得単価は、下記の約定実績（`data/purchase_history.json`）から自動算出し、ダッシュボードの「平均取得単価と含み損益」に現在の基準価額と並べて表示されます（`funds[].avg_cost_note`に注記を設定可能。判定・通知には使用しません）。約定単価が未確認の実績は`"price": null`で登録でき、その場合は平均取得単価の計算から除外され「約定単価が未記録の実績n件を除いた暫定値」の警告が出ます
- 購入実績（約定実績）の記録方法は上記「購入実績の記録方法」を参照
- S&P500の維持判断ルール（比率ベース）: `long_term_portfolio.share_review.threshold_percent`に、S&P500比率（S&P500が長期ポートフォリオ全体に占める割合）の基準（％）を設定すると、超えた場合にダッシュボードに「見直し検討」と表示されます（現在は暫定値50%。未設定の間は「基準の数値が未設定」。サテライト比率の目標`satellite_target_percent`とは別の数値です。表示のみで、自動売却・LINE通知はありません）

---

## 注意事項

- 本ツールは投資判断の自動化を行いません。**実際の買付注文はSBI証券での手動操作**です。
- データ取得には 日本経済新聞 電子版（日経電子版）の投資信託ページを利用しています。市場心理指標（VIX・米10年国債利回り・USD/JPY）も同じく日経電子版から取得します。
- 市場心理指標はダッシュボード（チャートページ）のみに参考情報として表示され、**LINE通知には含まれません**。BUY/WAIT等の判定は引き続き価格（Tier）のみで行われ、市場心理指標が判定ロジックに影響することはありません。
- 保有ポジション（監視専用。現状: はじめてのNISA・全世界株式インデックス／テスラ）は、暴落時の階層的追加投資（Tier）の対象ではありません。現在価格と取得単価の差（含み損益）を表示するだけで、BUY/WAIT判定ロジックには一切影響しません。米国株（テスラ）はYahoo!ファイナンスから取得しており（15分ディレイ）、日経電子版のデータとは取得元が異なります。米国株式市場も土日は休場のため、Tier監視の投資信託と同じく平日のみ実行されます。
- 設定来高値（ピーク）の記録は `config/settings.json` の `peak_start_date`（既定: 2026-07-07、購入判定基準日 `baseline.date` と同一日）以降のみ行われます。それより前は下落率・Tierは計測されません。`peak_start_date`を過去日付に変更した場合、`history.csv`に記録済みの履歴も自動的に遡って確認し、本日の値より高い記録があればそちらを初期ピークとして採用します。
- ピーク追跡の開始（`peak_start_date`）と実際の資金投入期間（`periods.phase2.start`、既定: 2026-08-01）は別の設定です。ピーク追跡開始後は、資金投入期間が始まる前（「②期間開始前」表示中）でもTier到達・LINE通知が発生し得ます。
- Tracers S&P500トップ10（2026-09-19導入）の設定来高値は、基準日（2026-09-18）の基準価額16,416円と監視開始後の実際の基準価額のうち高い方を起点に記録されます（それ以前の履歴は存在しないため）。判定基準日価格は`baseline.prices.tracers`＝2026-09-18の基準価額16,416円（基準日は銘柄別に`baseline.dates.tracers`で個別指定。Tracersの高値起算もこの日）としています。
- 実行日時の判定はすべてJST（日本標準時）基準です。GitHub Actionsの実行環境はUTCですが、スクリプト内でJSTに変換して日付・曜日を判定するため、土日（JST基準）は自動的に処理をスキップします。
- 投資はすべて自己責任でお願いします。
