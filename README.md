# 暴落監視ダッシュボード

新NISA攻撃フェーズ 暴落対応ルール監視システム

## 概要

| 項目 | 内容 |
|---|---|
| 実行方式 | GitHub Actions 平日自動実行（JST 07:00。実際の着信はGitHub Actions側の遅延により前後する） |
| 公開URL | GitHub Pages（セットアップ後に確定） |
| 通知 | LINE Messaging API（Tier到達時アラート ＋ 平日毎朝の日次サマリー） |
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

## ファイル構成

```
.
├── .github/
│   └── workflows/
│       └── monitor.yml         # GitHub Actions 定時実行ワークフロー
├── config/
│   └── settings.json           # 銘柄設定・Tier閾値・原資金額（ここを編集）
├── data/
│   ├── history.csv             # 日次基準価額履歴（自動蓄積）
│   ├── peak.json               # 設定来高値記録（自動更新）
│   ├── triggered.json          # 発動済みTier記録（重複通知防止）
│   ├── market.json             # 市場心理指標（VIX/米10年金利/USD-JPY）の最新値（自動更新）
│   ├── positions.json          # 保有ポジション（監視専用）の最新値（自動更新）
│   ├── positions_history.csv   # 保有ポジションの日次価格履歴（自動蓄積）
│   └── purchase_history.json   # Tier到達時の手動購入実績（運用者が手動追記）
├── scripts/
│   ├── monitor.py              # メイン実行スクリプト
│   ├── fetch_nav.py            # 基準価額取得
│   ├── market_data.py          # 市場心理指標（VIX/米10年金利/USD-JPY）取得・判定
│   ├── positions.py            # 保有ポジション（Tier投資対象外）取得・含み損益判定
│   ├── purchase_history.py     # 手動購入実績（約定実績）の読み込み
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
- 平日毎朝、LINEに日次サマリーが届く（Tier到達時はさらにアラート通知も届く）
- ダッシュボードURLはブックマーク登録推奨

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
- Tier到達時の手動購入実績（いつ・いくらで買ったか）は `data/purchase_history.json` に銘柄IDごとのリストとして手動で追記します。1件は `{"date": "2026-08-03", "price": 42266, "category": "Tier1", "amount": 33316}` の形式（`category`は`Tier1`〜`Tier3`のほか「定期積立」等の自由記述も可）。追記すると、次回のダッシュボード生成時に該当銘柄の推移チャート（該当銘柄のタブ選択時のみ。Tier1/2/3の閾値の破線とあわせて表示）に星マーカーとして自動反映されます。マーカーにカーソルを合わせると約定日・単価・区分・投入金額がツールチップで確認できます

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
