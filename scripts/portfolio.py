"""
portfolio.py
============
長期ポートフォリオ（攻撃フェーズ＋別枠積立＋旧つみたてNISA）の統合ビューを算出するモジュール。

保有している全ファンドの評価額を合算した「全体」に対する、各グループの割合を求める。

    評価額         = 保有口数 × 現在の基準価額 ÷ 10,000
    全体           = 全グループの評価額の合計
    サテライト比率 = サテライトのグループの評価額の合計 ÷ 全体
    グループ比率   = そのグループの評価額 ÷ 全体（例: S&P500比率）

グループの role は core（コア）/ satellite（サテライト）。どのファンドをどのグループ・口座に含めるか、
サテライト比率の目標、維持判断ルール（share_review）は config/settings.json の
long_term_portfolio で定義する（銘柄IDをコードに固定しない）。

購入実績（purchase_history.json）と現在の基準価額から計算するだけの表示専用の純粋関数で、
Tier判定・BUY/WAIT判定・LINE通知には一切使用しない。
"""

from purchase_history import calc_average_cost, filter_by_account


def _fund_names(settings: dict) -> dict:
    """銘柄ID→表示名（funds と positions.items の short_name）。"""
    names = {f["id"]: f.get("short_name", f["id"]) for f in settings.get("funds", [])}
    for item in settings.get("positions", {}).get("items", []):
        names.setdefault(item["id"], item.get("short_name", item["id"]))
    return names


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
          "groups": [{"id", "label", "role", "total_value": float|None, "share": float|None,
                      "fund_names": [str],
                      "holdings": [{"fund_id", "name", "account", "units", "value": float|None,
                                    "avg_cost": float|None, "count", "excluded", "nav"}]}],
          "total_value": float|None,          # 全グループの合計。基準価額が欠けると None
          "satellite_value": float|None,
          "satellite_ratio": float|None,      # %。サテライト÷全体
          "target_percent": float, "diff_pt": float|None,
          "satellite_names": [str],           # サテライトに含まれるグループ名
          "share_review": {"label", "fund_names", "value", "share", "threshold_percent",
                           "status": "keep"|"review"|"unset"|None, "note"} | None,
          "provisional": str|None,
        }
    """
    cfg = settings.get("long_term_portfolio")
    if not cfg or not cfg.get("enabled", True):
        return None

    names = _fund_names(settings)
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
                "name": names.get(fund_id, fund_id),
                "account": account,
                "units": units,
                "value": value,
                "avg_cost": avg["avg_cost"] if avg else None,
                "count": avg["count"] if avg else 0,
                "excluded": len(records) - (avg["count"] if avg else 0),
                "nav": nav,
            })
        fund_names = []
        for h in holdings:
            if h["name"] not in fund_names:
                fund_names.append(h["name"])
        groups.append({
            "id": g["id"],
            "label": g.get("label", g["id"]),
            "role": g.get("role", "core"),
            "total_value": group_total if group_ok else None,
            "fund_names": fund_names,
            "holdings": holdings,
        })

    calculable = all(g["total_value"] is not None for g in groups)
    total = sum(g["total_value"] for g in groups) if calculable else None
    for g in groups:
        g["share"] = g["total_value"] / total * 100 if calculable and total and total > 0 else None

    target = float(cfg.get("satellite_target_percent", 20))
    satellites = [g for g in groups if g["role"] == "satellite"]
    satellite = sum(g["total_value"] for g in satellites) if calculable else None
    ratio = satellite / total * 100 if calculable and total and total > 0 else None

    # 維持判断ルール（比率ベース）: 指定グループが全体に占める割合が、基準（ユーザーが事前に決めた数値）を
    # 超えたら「見直し検討」。数値が未設定なら "unset"。表示専用で、売却等の自動実行はしない。
    share_review = None
    review_cfg = cfg.get("share_review")
    if review_cfg and review_cfg.get("enabled", True):
        grp = next((g for g in groups if g["id"] == review_cfg.get("group_id")), None)
        thr = review_cfg.get("threshold_percent")
        share = grp["share"] if grp else None
        if share is None:
            status = None
        elif thr is None:
            status = "unset"
        else:
            status = "review" if share > thr else "keep"
        share_review = {
            "label": grp["label"] if grp else review_cfg.get("group_id"),
            "fund_names": grp["fund_names"] if grp else [],
            "value": grp["total_value"] if grp else None,
            "share": share,
            "threshold_percent": thr,
            "status": status,
            "note": review_cfg.get("note"),
        }

    return {
        "groups": groups,
        "total_value": total,
        "satellite_value": satellite,
        "satellite_ratio": ratio,
        "satellite_names": [g["label"] for g in satellites],
        "target_percent": target,
        "diff_pt": ratio - target if ratio is not None else None,
        "share_review": share_review,
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
