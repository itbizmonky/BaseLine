# 暴落監視ダッシュボード

新NISA 保有資産の「下落の気づき」監視システム

## 概要

| 項目 | 内容 |
|---|---|
| 実行方式 | GitHub Actions 平日自動実行（JST 07:00。実際の着信はGitHub Actions側の遅延により前後する） |
| 公開URL | GitHub Pages（セットアップ後に確定） |
| 通知 | LINE Messaging API（積立対象の銘柄が新しいTierに到達したときの下落アラート ＋ 平日毎朝の日次サマリー）。**LINEに載せるのは「最高比」（最高値比）と「平均比」（平均取得単価比）だけ**で、プラスは緑・マイナスは赤のダーク配色のFlexメッセージ（5つの枠ごとに見出しつき）（送信に失敗したときは🟢🔴つきのテキストで再送）。購入額・売買の指示・原資は含まない |
| 監視対象 | 保有する全資産（投資信託8本＋米国個別株2銘柄。`config/settings.json`の`funds`と`positions.items`） |
| 費用 | 無料（GitHub無料枠 / LINE Messaging API フリープラン） |
| UIデザイン | Cyber-Neumorphic (立体凹凸シャドウとネオン発光による近未来UI) |

投資判断は自動化しません。**実際の買付注文はSBI証券での手動操作**で、このツールは「気づき」を与えるだけです。追加買付（個別株・手動買付）に枠はなく、原資から賄います。

---

## 保有資産とTier閾値

Tierは最高値比の「下落の目安」（購入の指示ではありません）。しきい値は銘柄ごとに`config/settings.json`の`tiers`で設定します（未設定の銘柄はダッシュボードに「Tier未設定」と表示）。現在の値は、過去の下落頻度の目安（長期平均で Tier1 約1エピソード/年・Tier2 約0.3回/年・Tier3 約0.1回/年。暦年ごとに鳴るとは限らず、期間外検証では目標の1.5〜3倍多くなる傾向）で校正した**暫定値**で、通知の目盛りです（売買の推奨ではありません）。通知ログを見て約3か月後に見直し、約1年後に再校正します（履歴が短いSBI・V・はじめてのNISA・TSLA・NVDAと、代理指数のTracers・SOX・FANG+は特に不確実。TSLA・NVDAは通知対象外の参考値）。`role`は`core`（オルカン・S&P500系）／`satellite`（それ以外）／`separate`（子供用の別会計）、`alert: true`の銘柄だけがLINEのTierアラート対象です。

| 銘柄 | 区分 | LINEアラート | Tier1 | Tier2 | Tier3 |
|---|---|---|---|---|---|
| SBI・V・S&P500（旧NISA＋つみたて） | コア | − | -11% | -17% | -33% |
| eMAXIS Slim米国株式（S&P500） | コア | ○ | -11% | -17% | -33% |
| eMAXIS Slim全世界株式（オール・カントリー） | コア | ○ | -11% | -17% | -33% |
| Tracers S&P500トップ10インデックス | サテライト | ○ | -12% | -20% | -28% |
| ニッセイNASDAQ100インデックスファンド | サテライト | ○ | -12% | -25% | -31% |
| ニッセイSOX指数インデックスファンド（新規買付停止） | サテライト | − | -16% | -33% | -45% |
| iFreeNEXT FANG+インデックス（新規買付停止） | サテライト | − | -15% | -29% | -36% |
| はじめてのNISA・全世界株式（子供用・別会計） | 別会計 | − | -11% | -17% | -33% |
| テスラ（TSLA） | サテライト | − | -31% | -49% | -67% |
| エヌビディア（NVDA） | サテライト | − | -24% | -38% | -67% |

最高値の起点は、資産ごとに最初に購入した日（`baseline.dates`。既定は`peak_start_date`＝2026-07-07）です。履歴が無い期間は、約定単価も参考に初期の最高値を決めます。表示順は`funds`の並び順（投資信託）→ `positions.items`（米国株）です。

---

## ダッシュボードの主な表示

- **注意バナー・固定ナビ**: ページ最上部に、Tierに到達している銘柄（クリックで該当の詳細が開く）と取得失敗・前回値の資産を表示。ヘッダーの固定ナビ（サマリー／詳細／推移／市場／資金／保有）で各セクションへ移動でき、表示中のセクションを強調
- **ルールと見方 / 運用手順（サマリ）**: ページ上部の開閉式ガイド。数字の意味、LINE通知のルール、毎月の作業（約定履歴CSVの取り込み）、下落アラートが来たときの対応
- **投資意思決定サマリー**: 全保有銘柄を**5つの枠**に分けて表示（積立設定（原資枠）／積立設定（クレカ枠）／保持枠／旧NISA／こども枠。見出しに積立の月額合計）。各銘柄の前日比・平均取得単価比・Tier到達状況（最高値比は銘柄毎の詳細で確認）。スマホでも1銘柄1行のコンパクトな表（銘柄名の下に区分・月額などを小さく表示）。枠の判定は[枠のルール](#枠のルール)
- **銘柄毎の詳細（折りたたみ）**: 閉じた状態は1銘柄1行（銘柄名・現在値・最高値比・Tier）。タップで詳細を開き、「すべて開く／閉じる」も可。全保有銘柄の現在値（前日比つき）・設定来最高値（起点日つき）・最高値比（次のTierに達する価格つき）・平均取得単価（比）・評価額（構成比）・Tier到達状況
- **基準価額の推移とTier閾値**: 全銘柄の重ね表示と銘柄別タブ。銘柄タブではTier閾値（破線）と購入実績（星マーカー）を表示。米国株はUSD表示が既定で、ボタンで円換算に切り替え（過去分の円換算は当日の`usdjpy`列、なければ現在のレートによる近似）
- **市場心理（参考情報）**: VIX・米10年金利・USD/JPY（Tierの判定・通知には使用しない）
- **資金投入管理状況**: 原資で積立が続けられる月数・原資の使用率・NISA枠（成長／つみたて）の使用率。**割合と月数だけ**を表示（金額は載せない）
- **長期ポートフォリオ（ページ最下部）**: 保有資産の評価額（口数×現在値）を、枠ごとの小計つきで（枠内は大きい順に）表示し、コア（オルカン・S&P500系）／サテライト（それ以外）の割合を表示。目標・上限は置かず表示のみ。子供用（はじめてのNISA）は比率の分母に含めず別枠で表示

---

## 枠のルール

投資意思決定サマリー・長期ポートフォリオ・LINEの日次サマリーは、保有を次の5つの枠に分けて並べます（`scripts/groups.py`）。

| 枠 | 内容 |
|---|---|
| 積立設定（原資枠） | `monthly_schedule.cash`（現金積立）に設定されている銘柄 |
| 積立設定（クレカ枠） | `monthly_schedule.card`（クレジットカード積立）に設定されている銘柄 |
| 保持枠 | 積立設定のない保有（新規の積立をしていない銘柄・米国個別株） |
| 旧NISA | 旧つみたてNISA（`account=legacy`）の保有（SBI・V） |
| こども枠 | 子供用の別会計（`role=separate`。比率の分母に含めない） |

- 所属は`monthly_schedule`の**最新の設定**（SBIの積立設定画面の構成）から自動で決まります。積立設定を変えれば枠も変わります（最新の構成がまだ始まっていない場合は「◯/◯発注分からの設定」と注記）。
- 複数の枠に設定がある銘柄（例: NASDAQ100＝原資枠とクレカ枠）は両方の枠に出し、保有を資金源で分けます（原資枠＝原資買付分、クレカ枠＝カード積立分）。最高比・前日比・Tierは同じ値で、平均取得単価比・評価額は行ごとの値です。
- SBI・Vは、旧NISA分を「旧NISA」の行に分け、保持枠の行（つみたて分）は旧NISAを除いた保有で計算します。

## ファイル構成

```
.
├── .github/
│   └── workflows/
│       └── monitor.yml         # GitHub Actions 定時実行ワークフロー
├── config/
│   └── settings.json           # 資産・Tier閾値・積立設定・NISA枠など（ここを編集）
├── data/
│   ├── history.csv             # 投資信託の日次基準価額履歴（行の日付＝基準価額の基準日。自動蓄積。銘柄追加時は列が自動で増える）
│   ├── history_corrections.csv # 既存値と異なる再取得値の記録（上書きしない）
│   ├── peak.json               # 設定来高値記録（自動更新）
│   ├── triggered.json          # 通知済みTier記録（{資産ID: {"tier","date","notified":{Tier番号: 通知日}}}。Tierごとの武装・通知済み状態。重複通知防止）
│   ├── market.json             # 市場心理指標の最新値（自動更新）
│   ├── positions.json          # 米国株（TSLA・NVDA）の最新値（自動更新）
│   ├── positions_history.csv   # 米国株の日次終値（USD。行の日付＝取引日）とUSD/JPY（自動蓄積）
│   ├── purchase_history.json   # 購入実績（約定履歴から取り込む）
│   └── funding_status.json     # 資金管理（月数・使用率のみ。update_purchases.pyが更新）
├── scripts/
│   ├── monitor.py              # メイン実行スクリプト
│   ├── assets.py               # 全保有資産（funds＋米国株）を統一して扱う
│   ├── fetch_nav.py            # 投資信託の基準価額・基準日の取得（日経電子版）
│   ├── freshness.py            # 価格の鮮度判定（基準日の遅れ・stale判定）
│   ├── market_data.py          # 市場心理指標（VIX/米10年金利/USD-JPY）取得・判定
│   ├── positions.py            # 米国株の終値・取引日の取得（Yahoo Finance）・履歴保存
│   ├── purchase_history.py     # 購入実績の読み込み・平均取得単価（円・USD）の算出
│   ├── portfolio.py            # 長期ポートフォリオ（評価額・コア／サテライト比率）
│   ├── funding.py              # 資金管理（原資の月数・NISA枠の使用率）
│   ├── groups.py               # 保有を5つの枠にグルーピング（LINE・ダッシュボード共通）
│   ├── import_sbi_history.py   # SBI証券の約定履歴CSV（投信・米国株）を購入実績に取り込む
│   ├── update_purchases.py     # 取り込み→資金管理の更新→コミット・プッシュを1コマンドで
│   ├── migrate_purchase_history.py  # 購入実績をCSVに合わせて整備する一度きりの移行（dry-run既定）
│   ├── migrate_data_layout.py       # data/ を現在の構成に移す一度きりの移行（dry-run既定）
│   ├── judge.py                # 下落率・Tier判定・通知済みTier・最高値更新
│   ├── notify.py               # LINE通知
│   └── generate_dashboard.py   # HTMLダッシュボード生成
├── tests/                      # unittest（python -m unittest discover -s tests -v）
├── public/
│   └── index.html              # 生成されたダッシュボード（自動更新）
├── requirements.txt
└── README.md
```

`Personal/`（Git管理外）には、個人の計画・約定履歴CSV・原資の設定（`Personal/capital.json`）を置きます。公開リポジトリにはコミットしません。

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

### Step 4: 原資の設定（資金管理を使う場合）

原資の総額は公開しないため、リポジトリ外（Git管理外）の `Personal/capital.json` に置きます。

```json
{"initial_jpy": 3000000, "start_date": "2026-07-07"}
```

`update_purchases.py`（または`python scripts/funding.py`）が、約定履歴から「原資の使用率」と「積立が続けられる月数」を計算し、その結果（割合・月数だけ）を`data/funding_status.json`に保存します。ファイルがなければ原資の項目は更新せず、NISA枠の使用率だけ更新します。

### Step 5: 動作確認

1. **ローカルでのテスト（通知スキップ）:**
   ```bash
   pip install -r requirements.txt
   python scripts/monitor.py --dry-run
   # → public/index.html が生成されることを確認
   # --dry-run は土日でも実行可能（本番実行時のみJST基準で土日を自動スキップします）
   # --dry-run も data/*・public/index.html を書き換えます。確認後は git checkout で戻してください
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

4. **単体テスト:** `python -m unittest discover -s tests -v`

---

## 通常運用

- **毎朝JST 07:00**（平日のみ）に自動実行（GitHub Actions側の負荷状況により、実際の実行・通知は30〜60分程度遅れることがある）
- 手動で最新状態を確認したい場合は Actions → Run workflow（dry_run: false）
- 平日毎朝、LINEに日次サマリー（全保有銘柄の「最高比」「平均比」。プラスは緑・マイナスは赤のカード型）が届く。積立対象の銘柄（オルカン・S&P500・Tracers・NASDAQ100）が新しいTierに到達したときは、下落アラートも届く（Tier1〜3はそれぞれ独立して管理し、同じ下落局面では再通知しない。同時に複数のTierを越えたときは最も深いTierを1通だけ通知する。各Tierは、下落率がそのしきい値の半分（`alert.rearm_ratio`＝0.5）より浅くなると解除され、再び下落したときに通知される）
- 約定したら（積立日の数日後）、SBI証券から約定履歴CSV（投資信託・米国株）をダウンロードして`update_purchases.py`で取り込む（下記）
- ダッシュボードURLはブックマーク登録推奨

---

## 購入実績の記録方法

`data/purchase_history.json`は、SBI証券の約定履歴CSVから取り込みます（手作業は月1回、CSVのダウンロードだけです）。**CSVは個人の取引明細のため、公開リポジトリにコミットしないこと**（`Personal/`に置く）。

```bash
python scripts/update_purchases.py 約定履歴_投信.csv 約定履歴_米国株.csv
```

1. 取り込み予定（資産・約定日・単価・金額・口座区分・NISA枠・区分）を表示 → 確認（y/N）
2. `data/purchase_history.json`へ書き込み、平均取得単価（旧NISAを除く）を表示
3. 資金管理（原資の月数・NISA枠の使用率）を`data/funding_status.json`に更新
4. `purchase_history.json`と`funding_status.json`だけをコミット → 確認（y/N）→ リモートの日次更新を取り込んでプッシュ

`--yes`で確認を省略、`--no-push`でコミットまで。取り込みだけ行う場合は`python scripts/import_sbi_history.py <CSV...> [--dry-run] [--reclassify]`。重複は（銘柄・約定日・金額）の**件数照合**で判定するため、期間が重なるCSVでも何度でも実行でき、同じ日に同じ金額の約定が2件あっても取りこぼしません。既存の実績に口数・単価・枠が無ければ補完します。

### レコードの項目

```json
{
  "sox": [
    {"date": "2026-08-03", "price": 42266, "units": 7883, "amount": 33316,
     "category": "Tier1", "account": "attack", "frame": "growth"}
  ],
  "nvda": [
    {"date": "2026-11-12", "price": 175.0, "units": 2, "amount": 55982,
     "currency": "USD", "category": "追加買付", "account": "attack", "frame": "growth"}
  ]
}
```

| 項目 | 内容 |
|---|---|
| キー（資産ID） | `config/settings.json`の`id`（`sox`・`orkan`・`sbi_v_sp500`・`newnisa_orkan`・`tesla`・`nvda`など） |
| `date` / `amount` | 約定日／投入金額（受渡金額、円。米国株も円換算の受渡金額） |
| `price` | 約定単価。投資信託は基準価額（円／1万口）、米国株は株価（USD）。未確認なら`null`（平均取得単価から除外され警告が出る） |
| `units` | 投資信託は口数、米国株は株数。省略時は`投入金額÷約定単価×10,000`（投資信託のみ） |
| `currency` | `"USD"`（米国株）。省略時はJPY |
| `frame` | NISAの枠。`growth`（成長投資枠）／`tsumitate`（つみたて投資枠）。NISA枠の使用率の集計に使う |
| `category` | `Tier1`/`Tier2`/`Tier3`のほか`①分`・`定期積立`・`カード積立`・`追加買付`・`既存残高…`など自由（マーカーはTier区分で色分け: Tier1=黄／Tier2=橙／Tier3=赤／その他=青） |
| `account` | 資金源。`attack`＝原資から出した買付（現金積立・手動の追加買付・個別株。省略時）／`side`＝クレジットカードの積立（収入から。原資は消費しない）／`legacy`＝旧つみたてNISA（平均取得単価に含めない）／`child`＝子供用資金（別会計。原資・比率の分母に含めない） |

- 平均取得単価（投資信託）＝ Σ投入金額 ÷ Σ購入口数 × 10,000。米国株はUSD基準＝Σ(株数×USD単価) ÷ Σ株数（為替を含まない）。**旧つみたてNISA（`legacy`）を除く全口座**で計算します
- 口座区分は取り込み時に自動で決まります（子供用の銘柄→`child`、つみたて投資枠→`side`、成長投資枠でカード積立設定`monthly_schedule.card`と銘柄・金額が一致→`side`、それ以外→`attack`）。**CSVに決済方法の列がない**ため、同じ月に同じ銘柄・同じ金額でカード積立と手動買付が重なった場合は、日付が最も遅い約定をカード積立とみなして警告します（SBIの注文履歴の取引種別で確認してください。誤りは`purchase_history.json`の`account`を直接修正）
- 原資から出した約定の区分は日付から推定します（初回購入日→「①分」、毎月の積立日（23〜27日）→「定期積立」、それ以外→「追加買付」。規則は`sbi_import.attack_category_rules`）。Tierに到達した日の買付などの区分は、必要なら`category`を手動で書き換えてください（計算には影響しません）
- 「既存残高」（記録開始前の一括残高）は、2026年の約定を個別行で持つため、2025年以前の分だけを`既存残高（2025年以前）`として残します（一度きりの整備: `scripts/migrate_purchase_history.py`）

### 一度きりの移行スクリプト

| スクリプト | 内容 |
|---|---|
| `migrate_purchase_history.py <投信CSV> <米国株CSV> [--expect JSON] [--apply]` | 購入実績をCSVに合わせて整備（既存残高の二重計上の解消、口数・枠の補完、NVDA・TSLAの追加）。既定はdry-run。`--expect`でSBI保有画面の口数と照合 |
| `migrate_data_layout.py [--apply]` | `data/`を現在の構成へ（`history.csv`にSBI・V・はじめてのNISAの列を追加、`positions_history.csv`を米国株＋usdjpyに作り直し、Yahoo Financeから欠損を補完、`triggered.json`を新形式へ）。既定はdry-run |
| `migrate_history_asof.py [--apply]` | `history.csv`・`positions_history.csv`・`peak.json`の日付を、実行日から価格の基準日へ付け替える（一度きり。2026-11-25適用）。既定はdry-run。約定単価との一致率を出力し、値の不一致があれば`--apply`を拒否 |

---

## カスタマイズ

すべての設定は `config/settings.json` で変更可能：

- **資産の追加・停止**: 投資信託は`funds`、米国株は`positions.items`に1エントリ追加（`id`・`name`・`short_name`・`role`・`alert`・`tiers`など）。投資信託は`fund_code`（日経電子版）、米国株は`ticker`（Yahoo Finance）が必要。`history.csv`・`positions_history.csv`は列が自動で増える。新規買付を止める場合も、保有が残る限りエントリは残します
- **Tier閾値**: 資産ごとの`tiers`（最大3つ。未設定は`[]`）。LINEアラートの対象は`alert: true`の資産だけ。再通知の解除条件は`alert.rearm_ratio`（通知済みTierのしきい値×この値より下落率が浅くなったら解除）
- **価格の鮮度**: 価格の基準日（投信は日経ページの基準価格の日付、米国株は取引日）が直近の平日より`freshness.max_lag_weekdays`（既定3平日）を超えて古い場合は「判定保留」とし、最高値の更新・Tier判定・履歴追記をしません（LINEに取得エラーとして基準日つきで通知）。祝日・年末年始・GWの遅れは許容します。米国株は取引時間中の途中の価格を使わず、確定済みの終値を使います
- **最高値・基準日の起点**: `peak_start_date`（既定）と`baseline.dates`（資産ごと。最初に購入した日）、基準日の価格は`baseline.prices`
- **積立設定**: `monthly_schedule.cash`（現金積立）・`monthly_schedule.card`（クレカ積立）の`phases`に、`from`（発注日）つきの構成（`fund_id`・`amount`・`frame`）を追加。ダッシュボードの運用手順が当日に有効な構成と次の構成の予告を表示し、約定履歴の取り込みも`card`の設定でカード積立を判定します。**積立設定を変更したらここも更新してください**
- **NISA枠**: `nisa_limits`（成長240万円／つみたて120万円）
- **長期ポートフォリオ**: `role`（`core`/`satellite`/`separate`）で区分。`long_term_portfolio.enabled: false`で無効化
- 実行時刻・平日/毎日の切り替え（`schedule.hour_jst` / `schedule.minute_jst` / `schedule.daily`）
  - `daily: false` の場合、JST平日（月〜金）のみ実行されます
  - 変更内容は次回の `monitor.py` 実行時に `.github/workflows/monitor.yml` のcron設定へ自動反映されます（手動でcronを編集する必要はありません）
- 市場心理指標の取得元URL・VIXのしきい値（`market_indicators.sources` / `market_indicators.vix_thresholds`）。`market_indicators.enabled: false` で無効化

---

## 注意事項

- 本ツールは投資判断の自動化を行いません。**実際の買付注文はSBI証券での手動操作**です。
- 投資信託の基準価額と市場心理指標（VIX・米10年国債利回り・USD/JPY）は日本経済新聞 電子版から、米国株（TSLA・NVDA）の株価とUSD/JPYの履歴補完はYahoo Finance（米国）のチャートAPIから取得しています。Yahoo!ファイナンスJPのページは、2026-10以降に価格をJavaScriptで後から描画する構造に変わり、HTMLから取れなくなりました（TSLAの取得が10/20以降止まっていた原因）。取得に失敗した資産は前回値でダッシュボードを更新し、LINEに取得エラーを通知します。
- 市場心理指標はダッシュボードのみに参考情報として表示され、**LINE通知には含まれません**。Tierの判定は価格（最高値比）だけで行われます。
- 投資信託の基準価額は前営業日の海外市場終値をもとに決まります。履歴（`history.csv`・`positions_history.csv`）と最高値の日付は、**価格の基準日（投信＝基準価額の基準日、米国株＝取引日）**で記録します（2026-11-25に、実行日キーから付け替え。祝日・未更新の日は行が増えず、推移グラフの約定マーカーが価格の線に合います）。
- 設定来高値（ピーク）は、資産ごとの起点日（最初の購入日。`baseline.dates`、なければ`peak_start_date`）以降のみ記録されます。履歴に無い期間（例: SBI・V・S&P500の2026-01-06〜8月）は、記録済みの値と約定単価の最大値を初期の最高値にするため、真の最高値より低く出る可能性があります（カードに起点日を表示）。
- Tracers S&P500トップ10の基準日は、約定履歴（SBI証券）の最初の約定日`baseline.dates.tracers`＝2026-09-25、基準日価格は`baseline.prices.tracers`＝その約定単価16,941円です（最高値の起点も兼ねる。2026-11-25に、旧設定の2026-09-18・16,416円から一次情報に合わせて修正）。
- 実行日時の判定はすべてJST（日本標準時）基準です。GitHub Actionsの実行環境はUTCですが、スクリプト内でJSTに変換して日付・曜日を判定するため、土日（JST基準）は自動的に処理をスキップします。
- 公開リポジトリのため、個人の取引明細（約定履歴CSV）・原資の総額・家計情報はコミットしません（`.gitignore`で`Personal/`を除外）。`data/purchase_history.json`の保有口数・取得額は公開されます。
- 投資はすべて自己責任でお願いします。
