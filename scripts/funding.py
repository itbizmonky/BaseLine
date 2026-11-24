"""
funding.py
==========
資金管理の集計（原資で積立が続けられる月数・NISA枠の使用率）。

公開ページには「月数」と「使用率」だけを出す。原資の総額は公開リポジトリに載せないため、
このモジュールは約定履歴を取り込む手元（update_purchases.py の実行時）で計算し、結果
（月数・使用率）だけを data/funding_status.json に保存する。原資の総額は Personal/capital.json
（Git管理外）に置く:

    {"initial_jpy": 0, "start_date": "2026-07-07"}

- 原資の使用額 = 開始日以降の、原資から出した約定（account=attack。現金積立・手動の追加買付・個別株）の合計。
  クレジットカード積立（side）・旧NISA（legacy）・子供用（child）は原資を消費しない
- 積立が続けられる月数 = 原資の残額 ÷ 現金積立の月額（monthly_schedule.cash の現在有効な設定の合計）
- NISA枠の使用率 = 暦年内の約定のうち frame（growth/tsumitate）別の投入額 ÷ 年間投資枠（settings.json の nisa_limits）

    python scripts/funding.py        # 約定履歴から data/funding_status.json を更新する
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from import_sbi_history import phase_at  # noqa: E402
from purchase_history import ACCOUNT_ATTACK, FRAME_GROWTH, FRAME_TSUMITATE, load_purchase_history  # noqa: E402

ROOT = Path(__file__).parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.json"
CAPITAL_PATH = ROOT / "Personal" / "capital.json"
STATUS_PATH = ROOT / "data" / "funding_status.json"


def monthly_cash_amount(settings: dict, today_str: str) -> int:
    """現金積立の月額（円）。today_str 時点で有効な cash の設定の合計。設定がなければ 0。"""
    phase = phase_at(settings.get("monthly_schedule", {}).get("cash", {}).get("phases", []), today_str)
    return sum(it.get("amount", 0) for it in (phase or {}).get("items", []))


def calc_capital_status(purchase_history: dict, capital_cfg: dict, monthly_cash: int) -> dict:
    """
    原資の使用状況。
    Returns: {"used_jpy": int, "remaining_jpy": int, "used_percent": float, "months_remaining": float|None}
    （金額は計算用。公開ファイルには used_percent と months_remaining だけを保存する）
    """
    initial = capital_cfg["initial_jpy"]
    start = capital_cfg.get("start_date", "")
    used = sum(
        r.get("amount", 0)
        for records in purchase_history.values()
        for r in records
        if (r.get("account") or ACCOUNT_ATTACK) == ACCOUNT_ATTACK and r.get("date", "") >= start
    )
    remaining = initial - used
    return {
        "used_jpy": used,
        "remaining_jpy": remaining,
        "used_percent": used / initial * 100 if initial else 0.0,
        "months_remaining": remaining / monthly_cash if monthly_cash > 0 else None,
    }


def calc_nisa_usage(purchase_history: dict, year: int, limits: dict) -> dict:
    """
    暦年（約定日）のNISA枠の使用状況。
    Returns: {"growth": {"used_jpy", "limit_jpy", "percent"}, "tsumitate": {...}}
    frame が記録されていない実績は集計できないため無視する（migrate_purchase_history.py で補完する）。
    """
    prefix = f"{year}-"
    used = {FRAME_GROWTH: 0, FRAME_TSUMITATE: 0}
    for records in purchase_history.values():
        for r in records:
            if r.get("frame") in used and r.get("date", "").startswith(prefix):
                used[r["frame"]] += r.get("amount", 0)
    out = {}
    for frame in (FRAME_GROWTH, FRAME_TSUMITATE):
        limit = limits.get(frame, 0)
        out[frame] = {"used_jpy": used[frame], "limit_jpy": limit, "percent": used[frame] / limit * 100 if limit else 0.0}
    return out


def build_status(settings: dict, purchase_history: dict, capital_cfg: dict | None, today_str: str) -> dict:
    """公開用の資金管理の状態（月数・使用率だけ。金額は含めない）。capital_cfg が無ければ原資の項目は None。"""
    nisa = calc_nisa_usage(purchase_history, int(today_str[:4]), settings.get("nisa_limits", {}))
    capital = None
    if capital_cfg:
        cap = calc_capital_status(purchase_history, capital_cfg, monthly_cash_amount(settings, today_str))
        capital = {
            "used_percent": round(cap["used_percent"], 1),
            "months_remaining": round(cap["months_remaining"], 1) if cap["months_remaining"] is not None else None,
        }
    return {
        "as_of": today_str,
        "capital": capital,
        "nisa": {
            "year": int(today_str[:4]),
            "growth_percent": round(nisa[FRAME_GROWTH]["percent"], 1),
            "tsumitate_percent": round(nisa[FRAME_TSUMITATE]["percent"], 1),
        },
    }


def load_capital_config() -> dict | None:
    """Personal/capital.json（Git管理外）を読む。無ければ None（原資の項目は更新しない）。"""
    if not CAPITAL_PATH.exists():
        return None
    return json.loads(CAPITAL_PATH.read_text(encoding="utf-8"))


def load_status() -> dict | None:
    """data/funding_status.json を読む。無ければ None。"""
    if not STATUS_PATH.exists():
        return None
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def update_status(today_str: str | None = None) -> dict:
    """約定履歴から資金管理の状態を計算して data/funding_status.json に保存し、その内容を返す。"""
    today_str = today_str or date.today().isoformat()
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    capital_cfg = load_capital_config()
    if capital_cfg is None:
        print(f"⚠ {CAPITAL_PATH} がないため、原資の月数・使用率は更新しません（NISA枠の使用率だけ更新します）")
        previous = load_status() or {}
    status = build_status(settings, load_purchase_history(), capital_cfg, today_str)
    if capital_cfg is None:
        status["capital"] = (previous or {}).get("capital")
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    result = update_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
