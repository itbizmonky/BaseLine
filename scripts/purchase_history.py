"""
purchase_history.py
====================
Tier到達時の手動購入実績（約定実績）の読み込みモジュール。

judge.py の triggered.json（Tier到達＝通知済みという自動更新のstate）とは別物で、
こちらは運用者がSBI証券等での実際の買付後に手動で1件ずつ追記する「いつ・いくらで
買ったか」の記録。判定ロジック・通知フローには一切使用せず、ダッシュボードの
「基準価額の推移とTier閾値」グラフにマーカーとして重ね描画する表示専用データ。

data/purchase_history.json の形式（銘柄IDごとのレコードリスト）:
    {
      "sox": [
        {"date": "2026-08-03", "price": 42266, "category": "Tier1", "amount": 33316}
      ],
      "fang": [], "sp500": [], "orkan": [], "tracers": []
    }

新しい購入実績を追記する場合は、該当銘柄のリストに上記と同じ形式のオブジェクトを
1件追加してファイルを保存するだけでよい（次回のダッシュボード生成時に自動反映される）。
category は "Tier1"/"Tier2"/"Tier3" のほか、"定期積立" 等の自由な文言も指定できる。
price（約定単価）が未確認の場合は null にしておくと、平均取得単価の計算からは除外され
（ダッシュボードに「単価未記録」の警告が出る）、約定マーカーも描画されない。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PURCHASE_HISTORY_FILE = DATA_DIR / "purchase_history.json"


def load_purchase_history() -> dict:
    """
    purchase_history.json を読み込む。ファイルが存在しない場合は空dictを返す。
    銘柄IDは settings.json の funds に従って増減するため固定キーでの補完はしない
    （未登録銘柄は呼び出し側が .get(fund_id, []) で空扱いする）。
    """
    if not PURCHASE_HISTORY_FILE.exists():
        return {}
    with open(PURCHASE_HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def calc_average_cost(records: list[dict]) -> dict | None:
    """
    約定実績のリストから平均取得単価を算出する（基準価額は1万口あたり）。

        購入口数（各回） = 投入金額 ÷ 約定単価 × 10,000
        平均取得単価     = Σ投入金額 ÷ Σ購入口数 × 10,000

    約定単価（price）か投入金額（amount）が未記録・不正な実績は計算から除外し、その件数を
    excluded として返す（単価補完前に不正確な値が気付かれず使われるのを防ぐため）。

    Returns:
        None: 計算に使える実績が1件もない
        {"avg_cost": float, "total_amount": float, "total_units": float,
         "count": int, "excluded": int}
    """
    total_amount = 0.0
    total_units = 0.0
    count = 0
    excluded = 0
    for r in records:
        price = r.get("price")
        amount = r.get("amount")
        if not price or not amount or price <= 0 or amount <= 0:
            excluded += 1
            continue
        total_amount += amount
        total_units += amount / price * 10000
        count += 1
    if count == 0:
        return None
    return {
        "avg_cost": total_amount / total_units * 10000,
        "total_amount": total_amount,
        "total_units": total_units,
        "count": count,
        "excluded": excluded,
    }


def resolve_cost_basis(fallback: float, records: list[dict]) -> float:
    """
    保有ポジションの取得単価を決定する。約定実績（purchase_history.json）に計算可能な実績が
    あればその平均取得単価（複数回購入した場合の加重平均）を、なければ settings.json の
    cost_basis（fallback）を返す。
    """
    avg = calc_average_cost(records)
    return round(avg["avg_cost"]) if avg else fallback


def position_purchases(item: dict, records: list[dict]) -> list[dict]:
    """
    保有ポジションの購入実績（カード・チャートのマーカー表示用）を返す。
    約定実績（purchase_history.json）に日付つきの実績があればそれを、なければ settings.json の
    positions.items の purchase_date / purchase_amount（任意項目）を1件の実績として返す。
    各要素: {"date", "price", "amount", "category"}（priceは取得単価。不明ならNone）
    """
    dated = [r for r in records if r.get("date")]
    if dated:
        return [
            {"date": r["date"], "price": r.get("price"), "amount": r.get("amount"), "category": r.get("category", "")}
            for r in dated
        ]
    if item.get("purchase_date"):
        return [{
            "date": item["purchase_date"],
            "price": item.get("cost_basis"),
            "amount": item.get("purchase_amount"),
            "category": "",
        }]
    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    history = load_purchase_history()
    print("=== 約定実績 ===")
    for fund_id, records in history.items():
        if not records:
            continue
        print(f"  {fund_id}:")
        for r in records:
            price = f"{r['price']:,}円" if r.get("price") else "単価未記録"
            print(f"    {r['date']}  {price}  {r['category']}  {r['amount']:,}円")
        avg = calc_average_cost(records)
        if avg:
            print(f"    → 平均取得単価 {avg['avg_cost']:,.0f}円（{avg['count']}件、除外{avg['excluded']}件）")
