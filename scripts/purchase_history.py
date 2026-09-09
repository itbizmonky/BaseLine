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
      "fang": [], "sp500": [], "orkan": []
    }

新しい購入実績を追記する場合は、該当銘柄のリストに上記と同じ形式のオブジェクトを
1件追加してファイルを保存するだけでよい（次回のダッシュボード生成時に自動反映される）。
category は "Tier1"/"Tier2"/"Tier3" のほか、"定期積立" 等の自由な文言も指定できる。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PURCHASE_HISTORY_FILE = DATA_DIR / "purchase_history.json"


def load_purchase_history() -> dict:
    """
    purchase_history.json を読み込む。
    ファイルが存在しない、またはキーが欠けている場合は空リストで補完する。
    """
    default = {"fang": [], "sox": [], "sp500": [], "orkan": []}
    if not PURCHASE_HISTORY_FILE.exists():
        return default
    with open(PURCHASE_HISTORY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k in default:
        if k not in data:
            data[k] = []
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    history = load_purchase_history()
    print("=== 約定実績 ===")
    for fund_id, records in history.items():
        if not records:
            continue
        print(f"  {fund_id}:")
        for r in records:
            print(f"    {r['date']}  {r['price']:,}円  {r['category']}  {r['amount']:,}円")
