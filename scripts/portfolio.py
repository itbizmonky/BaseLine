"""
portfolio.py
============
長期ポートフォリオ（保有資産の合算）の統合ビュー。表示専用の純粋関数（Tier判定・LINE通知には使わない）。

- 評価額 = 保有口数（株数）× 現在値。保有口数は約定履歴（purchase_history.json）から集計する
  （旧つみたてNISA=legacy も含める）。米国株は USD株価 × USD/JPY で円換算する
- コア（role=core: オルカン・S&P500系）以外はサテライト（role=satellite）。目標・上限は置かない
- role=separate（はじめてのNISA＝子供用の別会計）は比率の分母に含めず、別枠で返す
- 現在値が取れない資産（または米国株でUSD/JPYが取れない場合）があると、その資産は missing に入り、
  比率（share）は算出できないため None を返す（一部を除外した偽の比率を出さない）
"""

import logging

from assets import ROLE_CORE, ROLE_SATELLITE, ROLE_SEPARATE, all_assets
from purchase_history import asset_holdings

logger = logging.getLogger(__name__)

ACCOUNT_LABELS = {"attack": "原資買付", "side": "カード積立", "legacy": "旧NISA", "child": "子供用"}


def asset_value(units: float, price: float | None, currency: str, usdjpy: float | None) -> float | None:
    """評価額（円）。現在値（USDの場合は為替も）が無ければ None。"""
    if price is None:
        return None
    if currency == "USD":
        return units * price * usdjpy if usdjpy else None
    return units * price / 10000


def build_integrated_view(
    settings: dict,
    purchase_history: dict,
    prices: dict[str, float | None],
    usdjpy: float | None = None,
) -> dict | None:
    """
    保有資産の統合ビューを返す。enabled が false なら None。

    Args:
        prices: {資産ID: 現在値}。投資信託は基準価額（円/1万口）、米国株は株価（USD）
        usdjpy: USD/JPY（米国株の円換算用）

    Returns:
        {
          "rows": [{"id","short_name","role","color","currency","units","price","value","share","accounts"}...]  # 評価額の大きい順（非別枠）
          "total_value": 全体の評価額（別枠を除く）| None,
          "core_value", "core_share", "satellite_value", "satellite_share",
          "separate": [{"id","short_name","value","units","price"}...],  # 別会計（子供用）
          "missing": [現在値または為替が取れず評価できない資産のshort_name...],
        }
    """
    cfg = settings.get("long_term_portfolio") or {}
    if not cfg.get("enabled", True):
        return None

    rows, separate, missing = [], [], []
    for asset in all_assets(settings):
        records = purchase_history.get(asset["id"], [])
        holdings = asset_holdings(records)
        if holdings["total_units"] <= 0:
            continue
        price = prices.get(asset["id"])
        value = asset_value(holdings["total_units"], price, asset["currency"], usdjpy)
        accounts = []
        for acc, h in sorted(holdings["by_account"].items()):
            acc_value = asset_value(h["units"], price, asset["currency"], usdjpy)
            accounts.append({"account": acc, "label": ACCOUNT_LABELS.get(acc, acc), "units": h["units"], "value": acc_value})
        row = {
            "id": asset["id"],
            "short_name": asset["short_name"],
            "role": asset["role"],
            "color": asset["color"],
            "currency": asset["currency"],
            "units": holdings["total_units"],
            "price": price,
            "value": value,
            "accounts": accounts,
        }
        if asset["role"] == ROLE_SEPARATE:
            separate.append(row)
            continue
        if value is None:
            missing.append(asset["short_name"])
        rows.append(row)

    complete = not missing
    total = sum(r["value"] for r in rows) if complete else None
    for r in rows:
        r["share"] = (r["value"] / total * 100) if total else None
    rows.sort(key=lambda r: r["value"] if r["value"] is not None else -1, reverse=True)

    def share_of(role: str) -> tuple[float | None, float | None]:
        if not complete:
            return None, None
        value = sum(r["value"] for r in rows if r["role"] == role)
        return value, (value / total * 100 if total else None)

    core_value, core_share = share_of(ROLE_CORE)
    sat_value, sat_share = share_of(ROLE_SATELLITE)
    return {
        "rows": rows,
        "total_value": total,
        "core_value": core_value,
        "core_share": core_share,
        "satellite_value": sat_value,
        "satellite_share": sat_share,
        "separate": separate,
        "missing": missing,
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent
    settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
    history = json.loads((root / "data" / "purchase_history.json").read_text(encoding="utf-8"))
    print("現在値を渡さずに呼ぶと全体は算出不可（missing に資産名）:")
    view = build_integrated_view(settings, history, {})
    print(" ", view["total_value"], view["missing"])
