# 暴落監視ダッシュボード

新NISA攻撃フェーズ 4銘柄 暴落対応ルール監視システム

## 概要

| 項目 | 内容 |
|---|---|
| 実行方式 | GitHub Actions 平日自動実行（JST 07:00） |
| 公開URL | GitHub Pages（セットアップ後に確定） |
| 通知 | LINE Messaging API（Tier到達時のみ） |
| 費用 | 無料（GitHub無料枠 / LINE Messaging API フリープラン） |

---

## 対象銘柄・Tier閾値

| 銘柄 | Tier1 | Tier2 | Tier3 |
|---|---|---|---|
| iFreeNEXT FANG+インデックス | ▲15% | ▲25% | ▲35% |
| ニッセイSOX指数インデックスファンド | ▲10% | ▲18% | ▲28% |
| eMAXIS Slim米国株式（S&P500） | ▲7% | ▲12% | ▲18% |
| eMAXIS Slim全世界株式（オール・カントリー） | ▲6% | ▲10% | ▲15% |

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
│   └── triggered.json          # 発動済みTier記録（重複通知防止）
├── scripts/
│   ├── monitor.py              # メイン実行スクリプト
│   ├── fetch_nav.py            # 基準価額取得
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

- **毎朝JST 07:00**（平日のみ）に自動実行
- 手動で最新状態を確認したい場合は Actions → Run workflow（dry_run: false）
- Tier到達時のみLINEに通知が届く
- ダッシュボードURLはブックマーク登録推奨

---

## カスタマイズ

すべての設定は `config/settings.json` で変更可能：

- 銘柄の追加・削除
- Tier閾値の変更
- 原資金額の変更
- 監視期間の変更

---

## 注意事項

- 本ツールは投資判断の自動化を行いません。**実際の買付注文はSBI証券での手動操作**です。
- データ取得には Yahoo!ファイナンス および各運用会社サイトを利用しています。
- 投資はすべて自己責任でお願いします。
