"""
purchase_history.py
====================
約定実績（購入実績）の読み込み・平均取得単価の算出モジュール（表示専用。Tier判定・LINE通知の判断には使わない）。

data/purchase_history.json の形式（資産IDごとのレコードリスト）:
    {
      "sox": [
        {"date": "2026-08-03", "price": 42266, "category": "Tier1", "amount": 33316,
         "account": "attack", "frame": "growth", "units": 7883}
      ],
      "nvda": [
        {"date": "2026-11-12", "price": 175.0, "units": 2, "amount": 55982,
         "currency": "USD", "account": "attack", "frame": "growth", "category": "追加買付"}
      ]
    }

各レコードの項目:
- date      : 約定日（YYYY-MM-DD）
- price     : 約定単価。投資信託は基準価額（円／1万口）、米国株は株価（USD／株）
- units     : 投資信託は口数、米国株は株数。省略時は 投入金額 ÷ 約定単価 × 10,000（投資信託のみ）
- amount    : 投入金額（円。米国株は円換算の受渡金額）
- currency  : "JPY"（省略時）／"USD"（米国株）
- account   : 資金源の区分（下記）。省略時は attack
- frame     : NISAの枠。"growth"（成長投資枠）／"tsumitate"（つみたて投資枠）。不明なら省略
- category  : "①分"・"Tier1"・"定期積立"・"カード積立"・"追加買付"・"既存残高…" など自由な文言

口座区分 account（資金源）:
- attack : 原資から出した買付（現金積立・手動の追加買付・個別株）
- side   : クレジットカードの積立（収入から。原資は消費しない）
- legacy : 旧つみたてNISA（新規買付不可。平均取得単価の計算には含めない）
- child  : 子供用資金（別会計。原資・長期ポートフォリオの比率には含めない）

price（約定単価）が未確認の場合は null にしておくと、平均取得単価の計算からは除外される
（ダッシュボードに「単価未記録」の警告が出る）。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PURCHASE_HISTORY_FILE = DATA_DIR / "purchase_history.json"

ACCOUNT_ATTACK = "attack"  # 原資から出した買付（現金積立・手動の追加買付・個別株）
ACCOUNT_SIDE = "side"      # クレジットカードの積立
ACCOUNT_LEGACY = "legacy"  # 旧つみたてNISA
ACCOUNT_CHILD = "child"    # 子供用資金（別会計）

FRAME_GROWTH = "growth"        # 成長投資枠
FRAME_TSUMITATE = "tsumitate"  # つみたて投資枠

CURRENCY_JPY = "JPY"
CURRENCY_USD = "USD"


def load_purchase_history() -> dict:
    """
    purchase_history.json を読み込む。ファイルが存在しない場合は空dictを返す。
    資産IDは settings.json に従って増減するため固定キーでの補完はしない
    （未登録の資産は呼び出し側が .get(asset_id, []) で空扱いする）。
    """
    if not PURCHASE_HISTORY_FILE.exists():
        return {}
    with open(PURCHASE_HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def record_account(r: dict) -> str:
    """実績の口座区分（資金源）を返す。account 省略時は attack（後方互換）。"""
    return r.get("account") or ACCOUNT_ATTACK


def record_currency(r: dict) -> str:
    """実績の通貨を返す。省略時は JPY。"""
    return r.get("currency") or CURRENCY_JPY


def filter_by_account(records: list[dict], account: str) -> list[dict]:
    """指定した口座区分の実績だけを返す。"""
    return [r for r in records if record_account(r) == account]


def filter_by_accounts(records: list[dict], accounts: list[str]) -> list[dict]:
    """指定した口座区分（複数可）の実績だけを返す。"""
    return [r for r in records if record_account(r) in accounts]


def cost_records(records: list[dict]) -> list[dict]:
    """
    平均取得単価・含み損益に使う実績（旧つみたてNISA=legacy を除く全口座）を返す。
    旧NISA分は非課税枠が復活しない別枠のため、新NISAの取得単価と混ぜない。
    """
    return [r for r in records if record_account(r) != ACCOUNT_LEGACY]


def record_units(r: dict) -> float | None:
    """
    実績1件の購入口数（米国株は株数）を返す。units が記録されていればそれを、なければ
    投入金額 ÷ 約定単価 × 10,000（投資信託のみ）で算出する。どちらも求められなければ None。
    """
    units = r.get("units")
    if units and units > 0:
        return float(units)
    if record_currency(r) == CURRENCY_USD:
        return None  # 株数が不明なら円と USD から逆算しない（為替が分からないため）
    price = r.get("price")
    amount = r.get("amount")
    if price and amount and price > 0 and amount > 0:
        return amount / price * 10000
    return None


def calc_average_cost(records: list[dict]) -> dict | None:
    """
    約定実績のリストから平均取得単価を算出する。

    投資信託（円。基準価額は1万口あたり）:
        平均取得単価 = Σ投入金額 ÷ Σ購入口数 × 10,000
        購入口数 = units があればそれ、なければ 投入金額 ÷ 約定単価 × 10,000
    米国株（USD。currency="USD" の実績）:
        平均取得単価（USD/株） = Σ(株数 × USD単価) ÷ Σ株数
        （円換算の平均取得単価は avg_cost_jpy に、受渡金額 ÷ 株数で返す）

    口数（株数）を求められない（約定単価も units も未記録）か、投入金額が不正な実績は計算から除外し、
    その件数を excluded として返す（不正確な値が気付かれず使われるのを防ぐため）。

    Returns:
        None: 計算に使える実績が1件もない
        {"avg_cost": float, "total_amount": float, "total_units": float,
         "count": int, "excluded": int, "currency": "JPY"|"USD", "avg_cost_jpy": float(米国株のみ)}
    """
    total_amount = 0.0
    total_units = 0.0
    total_usd = 0.0
    count = 0
    excluded = 0
    currencies = set()
    for r in records:
        amount = r.get("amount")
        units = record_units(r)
        cur = record_currency(r)
        if not amount or amount <= 0 or units is None:
            excluded += 1
            continue
        if cur == CURRENCY_USD:
            price = r.get("price")
            if not price or price <= 0:
                excluded += 1
                continue
            total_usd += units * price
        currencies.add(cur)
        total_amount += amount
        total_units += units
        count += 1
    if count == 0:
        return None
    if currencies == {CURRENCY_USD}:
        return {
            "avg_cost": total_usd / total_units,
            "avg_cost_jpy": total_amount / total_units,
            "total_amount": total_amount,
            "total_units": total_units,
            "count": count,
            "excluded": excluded,
            "currency": CURRENCY_USD,
        }
    return {
        "avg_cost": total_amount / total_units * 10000,
        "total_amount": total_amount,
        "total_units": total_units,
        "count": count,
        "excluded": excluded,
        "currency": CURRENCY_JPY,
    }


def calc_gain_vs_average(records: list[dict], nav: float | None) -> float | None:
    """
    現在値（投資信託は基準価額、米国株はUSD株価）の、平均取得単価に対する損益率（%）を返す。
    実績がない／現在値が不明なら None。米国株は為替を含まないUSD基準の損益率。
    records には cost_records() で旧NISA分を除いた実績を渡す。
    """
    avg = calc_average_cost(records)
    if avg is None or nav is None:
        return None
    return (nav - avg["avg_cost"]) / avg["avg_cost"] * 100


def asset_holdings(records: list[dict]) -> dict:
    """
    実績から保有口数（株数）と取得総額を集計する（口座区分ごとと合計）。
    Returns: {"total_units": float, "total_amount": float, "by_account": {account: {"units", "amount"}}}
    口数を求められない実績は無視する。
    """
    by_account: dict[str, dict] = {}
    for r in records:
        units = record_units(r)
        if units is None:
            continue
        acc = by_account.setdefault(record_account(r), {"units": 0.0, "amount": 0.0})
        acc["units"] += units
        acc["amount"] += r.get("amount") or 0
    return {
        "total_units": sum(a["units"] for a in by_account.values()),
        "total_amount": sum(a["amount"] for a in by_account.values()),
        "by_account": by_account,
    }


def purchase_markers(records: list[dict]) -> list[dict]:
    """
    チャートの約定マーカー用の実績（日付つき）を返す。
    各要素: {"date", "price", "amount", "category", "currency"}（priceは約定単価。不明ならNone）
    """
    return [
        {
            "date": r["date"],
            "price": r.get("price"),
            "amount": r.get("amount"),
            "category": r.get("category", ""),
            "currency": record_currency(r),
        }
        for r in records
        if r.get("date") and r.get("price")
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    history = load_purchase_history()
    print("=== 約定実績 ===")
    for asset_id, records in history.items():
        if not records:
            continue
        print(f"  {asset_id}:")
        for r in records:
            if r.get("price"):
                price = f"{r['price']:,.2f}USD" if record_currency(r) == CURRENCY_USD else f"{r['price']:,}円"
            else:
                price = "単価未記録"
            print(f"    {r['date']}  {price}  {r.get('category', '')}  {r['amount']:,}円  [{record_account(r)}]")
        avg = calc_average_cost(cost_records(records))
        if avg:
            unit = "USD" if avg["currency"] == CURRENCY_USD else "円"
            print(f"    → 平均取得単価 {avg['avg_cost']:,.2f}{unit}（{avg['count']}件、除外{avg['excluded']}件）")
