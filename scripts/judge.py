"""
judge.py
========
判定ロジックモジュール

- 下落率の計算（設定来高値比）
- Tier判定（Tier1/2/3 または 0=未到達）
- 既発動Tier記録（重複通知防止）
- 期間判定（②or③期間・残日数）
- 直近トレンド計算（↗↘→）
"""

import json
import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PEAK_FILE = DATA_DIR / "peak.json"
TRIGGERED_FILE = DATA_DIR / "triggered.json"
HISTORY_FILE = DATA_DIR / "history.csv"


# ------------------------------------------------------------------
# 高値・履歴 データI/O
# ------------------------------------------------------------------

def load_peak() -> dict:
    """設定来高値データを読み込む。ファイルがなければ空を返す。"""
    if not PEAK_FILE.exists():
        return {}
    with open(PEAK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_peak(peak: dict) -> None:
    """設定来高値データを保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(PEAK_FILE, "w", encoding="utf-8") as f:
        json.dump(peak, f, ensure_ascii=False, indent=2)


def load_triggered() -> dict:
    """発動済みTier履歴を読み込む。ファイルがなければ全ファンド空リストで返す。"""
    default = {"fang": [], "sox": [], "sp500": [], "orkan": []}
    if not TRIGGERED_FILE.exists():
        return default
    with open(TRIGGERED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # キー不足時の補完
    for k in default:
        if k not in data:
            data[k] = []
    return data


def save_triggered(triggered: dict) -> None:
    """発動済みTier履歴を保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(TRIGGERED_FILE, "w", encoding="utf-8") as f:
        json.dump(triggered, f, ensure_ascii=False, indent=2)


def load_history() -> list[dict]:
    """
    history.csv を読み込み、辞書のリストで返す。
    各行: {"date": "2026-08-01", "fang": 12345.0, ...}
    """
    if not HISTORY_FILE.exists():
        return []
    rows = []
    with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {"date": row["date"]}
            for fund_id in ("fang", "sox", "sp500", "orkan"):
                try:
                    parsed[fund_id] = float(row[fund_id]) if row.get(fund_id) else None
                except (ValueError, KeyError):
                    parsed[fund_id] = None
            rows.append(parsed)
    return rows


def append_history(today_str: str, navs: dict) -> None:
    """
    今日のNAVを history.csv に追記する。
    同日付がすでにある場合は上書きしない（べき等）。
    """
    DATA_DIR.mkdir(exist_ok=True)
    existing = load_history()
    existing_dates = {r["date"] for r in existing}

    if today_str in existing_dates:
        logger.info(f"history.csv: {today_str} はすでに存在するためスキップ")
        return

    fieldnames = ["date", "fang", "sox", "sp500", "orkan"]
    file_exists = HISTORY_FILE.exists()

    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row = {"date": today_str}
        for fund_id in ("fang", "sox", "sp500", "orkan"):
            nav = navs.get(fund_id)
            row[fund_id] = f"{nav:.0f}" if nav is not None else ""
        writer.writerow(row)

    logger.info(f"history.csv に {today_str} のデータを追記しました")


# ------------------------------------------------------------------
# 設定来高値 更新
# ------------------------------------------------------------------

def update_peak(peak: dict, navs: dict, today_str: str, peak_start_date: str) -> tuple[dict, list[str]]:
    """
    各ファンドの設定来高値を更新する。
    監視開始日（peak_start_date）以降のデータのみ高値更新の対象とする。

    Returns:
        (updated_peak, updated_fund_ids)
    """
    if today_str < peak_start_date:
        logger.info(f"今日 {today_str} は高値更新対象期間外 (開始: {peak_start_date})")
        return peak, []

    updated_ids = []
    for fund_id, nav in navs.items():
        if nav is None:
            continue
        current_peak = peak.get(fund_id, {}).get("value", 0)
        if nav > current_peak:
            peak[fund_id] = {"value": nav, "date": today_str}
            updated_ids.append(fund_id)
            logger.info(f"{fund_id}: 設定来高値を更新 {current_peak} → {nav}円 ({today_str})")

    return peak, updated_ids


# ------------------------------------------------------------------
# 下落率・Tier判定
# ------------------------------------------------------------------

def calc_drawdown(current_nav: float, peak_nav: float) -> float:
    """
    下落率を計算する（%で返す）。
    下落率 = (設定来高値 - 当日基準価額) / 設定来高値 × 100
    """
    if peak_nav <= 0:
        return 0.0
    return (peak_nav - current_nav) / peak_nav * 100


def judge_tier(drawdown: float, thresholds: list[int]) -> int:
    """
    下落率からTier番号を判定する。

    Args:
        drawdown: 下落率（%、正の値）
        thresholds: [Tier1閾値, Tier2閾値, Tier3閾値]（例: [15, 25, 35]）

    Returns:
        到達最大Tier番号 (0=未到達, 1, 2, 3)
    """
    tier = 0
    for i, threshold in enumerate(thresholds, start=1):
        if drawdown >= threshold:
            tier = i
    return tier


def calc_baseline_ratio(current_nav: float, baseline_nav: float) -> float:
    """
    基準日比（上昇/下落率）を計算する（%で返す）。
    (現在価格 - 基準日価格) / 基準日価格 * 100
    """
    if baseline_nav <= 0:
        return 0.0
    return (current_nav - baseline_nav) / baseline_nav * 100


def judge_decision(tier: int, current_nav: float, baseline_nav: float, tolerance_pct: float, is_high_water_mark: bool) -> str:
    """
    購入判定を行う。
    - HIGH: 高値更新中
    - BUY: Tier到達 かつ 基準日価格＋許容上昇率以内
    - WAIT: Tier到達 だが 基準日価格＋許容上昇率より高い
    - HOLD: Tier未到達
    """
    if is_high_water_mark:
        return "HIGH"
    
    if tier > 0:
        if baseline_nav > 0:
            threshold_price = baseline_nav * (1 + tolerance_pct / 100.0)
            if current_nav <= threshold_price:
                return "BUY"
            else:
                return "WAIT"
        else:
            return "BUY"
    
    return "HOLD"


def is_new_trigger(fund_id: str, current_tier: int, triggered: dict) -> bool:
    """
    新規Tier到達かどうかを判定する（重複通知防止）。
    current_tier が 0 の場合は False を返す。
    """
    if current_tier == 0:
        return False
    already_triggered = triggered.get(fund_id, [])
    return current_tier not in already_triggered


def record_trigger(fund_id: str, tier: int, triggered: dict) -> dict:
    """発動済みTierを記録する（破壊的変更）。"""
    if fund_id not in triggered:
        triggered[fund_id] = []
    if tier not in triggered[fund_id]:
        triggered[fund_id].append(tier)
        triggered[fund_id].sort()
    return triggered


# ------------------------------------------------------------------
# 期間判定
# ------------------------------------------------------------------

def detect_period(today: date, settings: dict) -> dict:
    """
    今日が②or③のどの期間にあるかを判定する。

    Returns:
        {
            "phase": "phase2" | "phase3" | "extension" | "none",
            "label": "②期間" | "③期間" | "延長期間" | "監視外期間",
            "days_remaining": int,
            "end_date": date,
        }
    """
    periods = settings["periods"]

    def parse_date(s: str) -> date:
        return datetime.strptime(s, "%Y-%m-%d").date()

    p2_start = parse_date(periods["phase2"]["start"])
    p2_end   = parse_date(periods["phase2"]["end"])
    p3_start = parse_date(periods["phase3"]["start"])
    p3_end   = parse_date(periods["phase3"]["end"])
    ext_end  = parse_date(periods["extension_end"])

    if p2_start <= today <= p2_end:
        return {
            "phase": "phase2",
            "label": "②期間",
            "days_remaining": (p2_end - today).days,
            "end_date": p2_end,
        }
    elif p3_start <= today <= p3_end:
        return {
            "phase": "phase3",
            "label": "③期間",
            "days_remaining": (p3_end - today).days,
            "end_date": p3_end,
        }
    elif p2_end < today < p3_start or today > p3_end:
        # 延長期間チェック
        if today <= ext_end:
            return {
                "phase": "extension",
                "label": "延長期間",
                "days_remaining": (ext_end - today).days,
                "end_date": ext_end,
            }
    return {
        "phase": "none",
        "label": "監視外期間",
        "days_remaining": 0,
        "end_date": today,
    }


# ------------------------------------------------------------------
# トレンド計算
# ------------------------------------------------------------------

def calc_trend(history: list[dict], fund_id: str, days: int = 5) -> str:
    """
    直近 N 営業日分の NAV トレンドを計算する。

    Returns:
        "↗" (上昇) / "↘" (下落) / "→" (横ばい)
    """
    # NAVが入っている行だけ抽出
    nav_rows = [
        r for r in history
        if r.get(fund_id) is not None
    ]
    if len(nav_rows) < 2:
        return "→"

    recent = nav_rows[-days:] if len(nav_rows) >= days else nav_rows
    first_nav = recent[0][fund_id]
    last_nav  = recent[-1][fund_id]

    if first_nav == 0:
        return "→"

    change_pct = (last_nav - first_nav) / first_nav * 100

    if change_pct > 0.5:
        return "↗"
    elif change_pct < -0.5:
        return "↘"
    else:
        return "→"


# ------------------------------------------------------------------
# 残資金計算
# ------------------------------------------------------------------

def calc_remaining_funds(fund_id: str, triggered: dict, phase_key: str, settings: dict) -> dict:
    """
    指定ファンド・期間の残資金（投入済み/未投入）を計算する。

    Returns:
        {"invested": int, "remaining": int, "total": int, "tier_detail": {...}}
    """
    phase_funds = settings.get(f"funds_{phase_key}", {})
    tier_amounts = phase_funds.get("tier_amounts", {}).get(fund_id, {})
    fired_tiers = triggered.get(fund_id, [])

    invested = sum(
        tier_amounts.get(f"tier{t}", 0)
        for t in fired_tiers
    )
    total = sum(tier_amounts.values()) if tier_amounts else 0
    remaining = total - invested

    tier_detail = {}
    for i in range(1, 4):
        key = f"tier{i}"
        amount = tier_amounts.get(key, 0)
        tier_detail[f"tier{i}"] = {
            "amount": amount,
            "invested": i in fired_tiers,
        }

    return {
        "invested": invested,
        "remaining": remaining,
        "total": total,
        "tier_detail": tier_detail,
    }


# ------------------------------------------------------------------
# スタンドアローン実行（テスト用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # テストデータ
    test_navs = {"fang": 11000, "sox": 8910, "sp500": 24000, "orkan": 21000}
    test_peak = {
        "fang":  {"value": 13200, "date": "2026-09-10"},
        "sox":   {"value": 9900,  "date": "2026-09-08"},
        "sp500": {"value": 26000, "date": "2026-09-12"},
        "orkan": {"value": 23500, "date": "2026-09-12"},
    }

    import json
    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    with open(settings_path) as f:
        settings = json.load(f)

    print("=== 下落率・Tier判定テスト ===")
    for fund in settings["funds"]:
        fid = fund["id"]
        nav = test_navs.get(fid)
        peak_val = test_peak.get(fid, {}).get("value", nav)
        if nav is None or peak_val is None:
            continue
        dd = calc_drawdown(nav, peak_val)
        tier = judge_tier(dd, fund["tiers"])
        print(f"  {fund['short_name']:10s}: 下落率={dd:.2f}% → Tier{tier} (閾値: {fund['tiers']}%)")

    print("\n=== 期間判定テスト ===")
    today = date(2026, 10, 15)
    period_info = detect_period(today, settings)
    print(f"  {today}: {period_info['label']} / 残{period_info['days_remaining']}日")
