# 暴落監視ダッシュボード

新NISA攻撃フェーズ 4銘柄 暴落対応ルール監視システム

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
| iFreeNEXT FANG+インデックス | -15% | -25% | -35% |
| ニッセイSOX指数インデックスファンド | -10% | -18% | -28% |
| eMAXIS Slim米国株式（S&P500） | -7% | -12% | -18% |
| eMAXIS Slim全世界株式（オール・カントリー） | -6% | -10% | -15% |

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
│   └── positions_history.csv   # 保有ポジションの日次価格履歴（自動蓄積）
├── scripts/
│   ├── monitor.py              # メイン実行スクリプト
│   ├── fetch_nav.py            # 基準価額取得
│   ├── market_data.py          # 市場心理指標（VIX/米10年金利/USD-JPY）取得・判定
│   ├── positions.py            # 保有ポジション（Tier投資対象外）取得・含み損益判定
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
    "fang":  {"tier1": 200000, "tier2": 300000, "tier3": 400000},
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

---

## 注意事項

- 本ツールは投資判断の自動化を行いません。**実際の買付注文はSBI証券での手動操作**です。
- データ取得には 日本経済新聞 電子版（日経電子版）の投資信託ページを利用しています。市場心理指標（VIX・米10年国債利回り・USD/JPY）も同じく日経電子版から取得します。
- 市場心理指標はダッシュボード（チャートページ）のみに参考情報として表示され、**LINE通知には含まれません**。BUY/WAIT等の判定は引き続き価格（Tier）のみで行われ、市場心理指標が判定ロジックに影響することはありません。
- 保有ポジション（監視専用。現状: はじめてのNISA・全世界株式インデックス／テスラ）は、暴落時の階層的追加投資（Tier）の対象ではありません。現在価格と取得単価の差（含み損益）を表示するだけで、BUY/WAIT判定ロジックには一切影響しません。米国株（テスラ）はYahoo!ファイナンスから取得しており（15分ディレイ）、日経電子版のデータとは取得元が異なります。米国株式市場も土日は休場のため、既存4銘柄と同じく平日のみ実行されます。
- 設定来高値（ピーク）の記録は `config/settings.json` の `peak_start_date`（既定: 2026-07-07、購入判定基準日 `baseline.date` と同一日）以降のみ行われます。それより前は下落率・Tierは計測されません。`peak_start_date`を過去日付に変更した場合、`history.csv`に記録済みの履歴も自動的に遡って確認し、本日の値より高い記録があればそちらを初期ピークとして採用します。
- ピーク追跡の開始（`peak_start_date`）と実際の資金投入期間（`periods.phase2.start`、既定: 2026-08-01）は別の設定です。ピーク追跡開始後は、資金投入期間が始まる前（「②期間開始前」表示中）でもTier到達・LINE通知が発生し得ます。
- 実行日時の判定はすべてJST（日本標準時）基準です。GitHub Actionsの実行環境はUTCですが、スクリプト内でJSTに変換して日付・曜日を判定するため、土日（JST基準）は自動的に処理をスキップします。
- 投資はすべて自己責任でお願いします。
