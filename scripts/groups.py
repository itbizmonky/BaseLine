"""
groups.py
=========
保有資産を5つの「枠」にグルーピングする（LINE・ダッシュボードで共通に使う）。

    1. 積立設定（原資枠）  : 現金積立（`monthly_schedule.cash`）に設定されている銘柄
    2. 積立設定（クレカ枠）: クレジットカード積立（`monthly_schedule.card`）に設定されている銘柄
    3. 保持枠              : 積立設定のない保有（新規の積立をしていない銘柄・米国個別株など）
    4. 旧NISA              : 旧つみたてNISA（`account=legacy`）の保有
    5. こども枠            : 子供用の別会計（`role=separate`）

- 所属は `monthly_schedule` の**最新の設定**（SBIの積立設定画面の構成）から自動で決まる。積立設定を変えれば枠も変わる
- 複数の枠に設定がある銘柄（例: NASDAQ100＝原資枠とクレカ枠）は、両方の枠に行を出す。保有は資金源で分けて、
  原資枠の行は「原資買付分」（`account=attack`）、クレカ枠の行は「カード積立分」（`account=side`）。
  価格に関する指標（最高比・前日比・Tier）は同じ値で、平均取得単価比・評価額は行ごと（資金源ごと）の値
- 旧NISAを持つ銘柄（SBI・V）は、旧NISA分を「旧NISA」の行に分け、他の行は旧NISAを除いた保有で計算する
- 表示専用。Tier判定・通知の判断には使わない
"""

from assets import ROLE_SEPARATE, all_assets
from portfolio import asset_value
from purchase_history import (
    ACCOUNT_ATTACK, ACCOUNT_LEGACY, ACCOUNT_SIDE, calc_gain_vs_average, cost_records, record_account, record_units,
)

GROUP_DEFS = [
    ("cash", "積立設定（原資枠）"),
    ("card", "積立設定（クレカ枠）"),
    ("hold", "保持枠"),
    ("legacy", "旧NISA"),
    ("child", "こども枠"),
]


def _latest_phase(settings: dict, key: str) -> dict | None:
    phases = settings.get("monthly_schedule", {}).get(key, {}).get("phases", [])
    return max(phases, key=lambda p: p["from"]) if phases else None


def schedule_amounts(settings: dict, key: str) -> dict[str, int]:
    """積立設定（cash / card）の最新の構成の、銘柄ごとの月額（円）。"""
    phase = _latest_phase(settings, key)
    out: dict[str, int] = {}
    for item in (phase or {}).get("items", []):
        out[item["fund_id"]] = out.get(item["fund_id"], 0) + item["amount"]
    return out


def schedule_note(settings: dict, key: str, today_str: str) -> str | None:
    """最新の構成がまだ始まっていない（from が今日より先）場合の注記。例: 「2027/1/10発注分からの設定」。"""
    phase = _latest_phase(settings, key)
    if phase and phase["from"] > today_str:
        y, m, d = phase["from"].split("-")
        return f"{y}/{int(m)}/{int(d)}発注分からの設定"
    return None


def build_groups(settings: dict, purchases: dict, results: list[dict], usdjpy: float | None, today_str: str = "") -> list[dict]:
    """
    5つの枠と、枠ごとの行（銘柄×資金源）を返す。行は monitor の results の項目に、次を足したもの:
        monthly（積立の月額。積立設定の行のみ）, tag（行の補足。例「原資買付分」）, units, value（評価額・円。評価不可はNone）,
        avg_cost_ratio（その行の保有に対する平均取得単価比）
    各枠: {"key","label","lines","monthly_total"(積立設定のみ),"note","value","share"}
    value は枠内の評価額の合計（1行でも評価不可ならNone）、share は子供用を除く全体に対する割合（こども枠はNone）。
    """
    by_id = {r["asset_id"]: r for r in results}
    assets = [a for a in all_assets(settings) if a["id"] in by_id]
    amounts = {"cash": schedule_amounts(settings, "cash"), "card": schedule_amounts(settings, "card")}
    slice_accounts = {"cash": {ACCOUNT_ATTACK}, "card": {ACCOUNT_SIDE}}
    groups = {key: [] for key, _ in GROUP_DEFS}

    def make_line(asset: dict, accounts: set | None, monthly: int | None = None, tag: str = "") -> dict:
        r = by_id[asset["id"]]
        records = purchases.get(asset["id"], [])
        selected = cost_records(records) if accounts is None else [x for x in records if record_account(x) in accounts]
        units = sum(record_units(x) or 0 for x in selected)
        price = r.get("price")
        value = asset_value(units, price, asset["currency"], usdjpy) if units > 0 else 0.0
        return {**r, "monthly": monthly, "tag": tag, "units": units, "value": value,
                "avg_cost_ratio": calc_gain_vs_average(selected, price)}

    for asset in assets:
        aid = asset["id"]
        records = purchases.get(aid, [])
        has_legacy = any(record_account(x) == ACCOUNT_LEGACY and (record_units(x) or 0) > 0 for x in records)
        if asset["role"] == ROLE_SEPARATE:
            groups["child"].append(make_line(asset, None))
            continue
        member = [k for k in ("cash", "card") if aid in amounts[k]]
        for key in member:
            if len(member) == 2:   # 複数の枠に設定がある銘柄は、資金源で保有を分ける
                tag = "原資買付分" if key == "cash" else "カード積立分"
                groups[key].append(make_line(asset, slice_accounts[key], amounts[key][aid], tag))
            else:
                groups[key].append(make_line(asset, None, amounts[key][aid], "つみたて分" if has_legacy else ""))
        if not member and sum(record_units(x) or 0 for x in cost_records(records)) > 0:
            groups["hold"].append(make_line(asset, None, None, "つみたて分" if has_legacy else ""))
        if has_legacy:
            groups["legacy"].append(make_line(asset, {ACCOUNT_LEGACY}))

    total_lines = [ln for key, lines in groups.items() if key != "child" for ln in lines]
    total = None if any(ln["value"] is None for ln in total_lines) else sum(ln["value"] for ln in total_lines)
    out = []
    for key, label in GROUP_DEFS:
        lines = groups[key]
        if not lines and key not in ("cash", "card"):
            continue
        value = None if any(ln["value"] is None for ln in lines) else sum(ln["value"] for ln in lines)
        out.append({
            "key": key,
            "label": label,
            "lines": lines,
            "monthly_total": sum(amounts[key].values()) if key in amounts else None,
            "note": schedule_note(settings, key, today_str) if key in amounts and today_str else None,
            "value": value,
            "share": (value / total * 100) if (value is not None and total and key != "child") else None,
        })
    return out
