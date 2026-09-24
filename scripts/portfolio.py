"""
portfolio.py
============
長期ポートフォリオ（攻撃フェーズ＋別枠積立）の統合ビューを算出するモジュール。

2028年月初に「攻撃フェーズと別枠積立を合算した長期ポートフォリオを、オルカン80%／Tracers20%へ
一括リバランス」する判断のため、同一ファンド（オルカン・Tracers・S&P500）について
攻撃フェーズ分と別枠積立分の評価額を合算し、サテライト比率を算出する。

    評価額           = 保有口数 × 現在の基準価額 ÷ 10,000
    サテライト比率   = (Tracers合計 + S&P500合計) ÷ (オルカン合計 + Tracers合計 + S&P500合計)

購入実績（purchase_history.json）と現在の基準価額から計算するだけの表示専用の純粋関数で、
Tier判定・BUY/WAIT判定・LINE通知には一切使用しない。グルーピングや目標比率は
config/settings.json の long_term_portfolio で定義する（銘柄IDをコードに固定しない）。
"""

from purchase_history import calc_average_cost, filter_by_account


def build_integrated_view(settings: dict, purchase_history: dict, prices: dict) -> dict | None:
    """
    統合ビューを算出する。

    Args:
        settings: config/settings.json の内容（long_term_portfolio を参照）
        purchase_history: data/purchase_history.json の内容（銘柄ID→実績リスト）
        prices: 銘柄ID→現在の基準価額（取得できない銘柄は None または欠落）

    Returns:
        long_term_portfolio が未設定・無効の場合は None。それ以外は
        {
          "groups": [{"id", "label", "role", "total_value": float|None,
                      "holdings": [{"fund_id", "account", "units", "value": float|None,
                                    "avg_cost": float|None, "count", "excluded", "nav"}]}],
          "total_value": float|None, "satellite_value": float|None,
          "satellite_ratio": float|None,   # %。基準価額が欠けて算出できない場合は None
          "target_percent": float, "diff_pt": float|None, "adjustment_amount": float|None,
          "provisional": str|None,
        }
    """
    cfg = settings.get("long_term_portfolio")
    if not cfg or not cfg.get("enabled", True):
        return None

    groups = []
    for g in cfg.get("groups", []):
        holdings = []
        group_total = 0.0
        group_ok = True
        for h in g.get("holdings", []):
            fund_id, account = h["fund_id"], h["account"]
            records = filter_by_account(purchase_history.get(fund_id, []), account)
            avg = calc_average_cost(records)
            nav = prices.get(fund_id)
            if avg is None:
                # 実績がなければ保有なし（評価額0円）として扱う
                units, value = 0.0, 0.0
            elif nav is None:
                # 保有はあるが現在の基準価額が得られない → 誤った合計を出さないため算出不可
                units, value = avg["total_units"], None
                group_ok = False
            else:
                units = avg["total_units"]
                value = units * nav / 10000
            if value is not None:
                group_total += value
            holdings.append({
                "fund_id": fund_id,
                "account": account,
                "units": units,
                "value": value,
                "avg_cost": avg["avg_cost"] if avg else None,
                "count": avg["count"] if avg else 0,
                "excluded": len(records) - (avg["count"] if avg else 0),
                "nav": nav,
            })
        groups.append({
            "id": g["id"],
            "label": g.get("label", g["id"]),
            "role": g.get("role", "core"),
            "total_value": group_total if group_ok else None,
            "holdings": holdings,
        })

    target = float(cfg.get("satellite_target_percent", 20))
    calculable = all(g["total_value"] is not None for g in groups)
    total = sum(g["total_value"] for g in groups) if calculable else None
    satellite = sum(g["total_value"] for g in groups if g["role"] == "satellite") if calculable else None
    ratio = satellite / total * 100 if calculable and total and total > 0 else None

    return {
        "groups": groups,
        "total_value": total,
        "satellite_value": satellite,
        "satellite_ratio": ratio,
        "target_percent": target,
        "diff_pt": ratio - target if ratio is not None else None,
        # 参考値: サテライトを目標比率に戻すために動かす金額（正=サテライト超過分、負=不足分）
        "adjustment_amount": satellite - target / 100 * total if ratio is not None else None,
        "provisional": cfg.get("provisional_note"),
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    from purchase_history import load_purchase_history

    root = Path(__file__).parent.parent
    settings = json.load(open(root / "config" / "settings.json", encoding="utf-8"))
    view = build_integrated_view(settings, load_purchase_history(), {})
    print(json.dumps(view, ensure_ascii=False, indent=2))
