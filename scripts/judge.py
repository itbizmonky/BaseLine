"""
judge.py
========
判定ロジックモジュール

- 下落率の計算（設定来高値比）
- Tier判定（Tier1/2/3 または 0=未到達）。Tierは「下落の気づき」の目安で、購入判定・投入額とは無関係
- 通知済みTierの記録と再通知の制御（Tierごとに独立した武装・通知済み状態。重複通知防止・回復後の再武装）
- 直近トレンド計算（↗↘→）
"""

import json
import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PEAK_FILE = DATA_DIR / "peak.json"
TRIGGERED_FILE = DATA_DIR / "triggered.json"
HISTORY_FILE = DATA_DIR / "history.csv"

# 通知済みTierを解除（再武装）する下落率の目安。通知済みTierのしきい値 × この値 より下落率が浅くなったら解除する
# （既定0.5＝しきい値の半分。settings.json の alert.rearm_ratio で上書きする。しきい値付近の往復で通知が数倍に増えるのを防ぐ）
DEFAULT_REARM_RATIO = 0.5


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


def load_history() -> list[dict]:
    """
    history.csv を読み込み、辞書のリストで返す。
    各行: {"date": "2026-08-01", "fang": 12345.0, ...}
    銘柄列はCSVヘッダに従う（銘柄の追加でヘッダが増えても対応。ヘッダにない銘柄は呼び出し側の
    r.get(fund_id) がNoneを返す）。
    """
    if not HISTORY_FILE.exists():
        return []
    rows = []
    with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fund_ids = [c for c in (reader.fieldnames or []) if c != "date"]
        for row in reader:
            parsed = {"date": row["date"]}
            for fund_id in fund_ids:
                try:
                    parsed[fund_id] = float(row[fund_id]) if row.get(fund_id) else None
                except ValueError:
                    parsed[fund_id] = None
            rows.append(parsed)
    return rows


def _read_history_header() -> list[str]:
    """history.csv の現在のヘッダ（列名）を返す。ファイルがなければ空リスト。"""
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        return next(csv.reader(f), [])


def append_history(today_str: str, navs: dict) -> None:
    """
    今日のNAVを history.csv に追記する。
    同日付がすでにある場合は上書きしない（べき等）。

    navs に既存ヘッダにない銘柄（新規追加銘柄）が含まれる場合は、ヘッダに列を追加して
    既存行を空欄で補完した上で追記する（列ずれを起こさないためのヘッダ移行）。
    """
    DATA_DIR.mkdir(exist_ok=True)
    existing = load_history()
    existing_dates = {r["date"] for r in existing}

    if today_str in existing_dates:
        logger.info(f"history.csv: {today_str} はすでに存在するためスキップ")
        return

    header = _read_history_header()
    fund_ids = [c for c in header if c != "date"]
    for fund_id in navs:
        if fund_id not in fund_ids:
            fund_ids.append(fund_id)
    fieldnames = ["date"] + fund_ids

    if header and header != fieldnames:
        with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
            raw_rows = list(csv.DictReader(f))
        with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
            writer.writeheader()
            writer.writerows(raw_rows)
        logger.info(f"history.csv のヘッダを移行しました: {header} → {fieldnames}")

    file_exists = HISTORY_FILE.exists()

    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        if not file_exists:
            writer.writeheader()
        row = {"date": today_str}
        for fund_id in fund_ids:
            nav = navs.get(fund_id)
            row[fund_id] = f"{nav:.0f}" if nav is not None else ""
        writer.writerow(row)

    logger.info(f"history.csv に {today_str} のデータを追記しました")


# ------------------------------------------------------------------
# 設定来高値 更新
# ------------------------------------------------------------------

def update_peak(
    peak: dict,
    navs: dict,
    today_str: str,
    peak_start_date: str,
    history: list[dict] | None = None,
    baseline: dict | None = None,
    extra_points: dict | None = None,
) -> tuple[dict, list[str]]:
    """
    各資産の設定来高値を更新する。
    資産ごとの起点日以降のデータだけを高値更新の対象とする。起点日は baseline.dates[資産ID]
    （その資産を最初に購入した日）で、なければ peak_start_date。

    資産の初回記録時は、起点日以降の履歴（history）と extra_points（約定単価など、履歴に無い期間の
    既知の価格 {資産ID: [(日付, 価格), ...]}）も確認し、本日の値より高い記録があればそちらを初期値に
    採用する（起点日を過去に設定した場合の取りこぼし防止）。

    さらに baseline（settings.jsonの基準日価格）が起点日以降かつ本日以前で、記録済みの高値を上回って
    いる場合は補正する（履歴の欠落時に、唯一残っている基準日時点の実績値を取りこぼさないため。
    毎回無条件に確認するので、初回記録済み以降のデータ欠落にも自己修復的に対応できる）。

    Returns:
        (updated_peak, updated_asset_ids)
    """
    default_baseline_date = (baseline or {}).get("date")
    baseline_dates = (baseline or {}).get("dates", {})
    baseline_prices = (baseline or {}).get("prices", {})

    updated_ids = []
    for asset_id, nav in navs.items():
        if nav is None:
            continue
        start = baseline_dates.get(asset_id, peak_start_date)
        if today_str < start:
            logger.info(f"{asset_id}: 今日 {today_str} は高値更新対象期間外 (起点: {start})")
            continue

        if asset_id not in peak:
            seed = seed_initial_peak(history or [], asset_id, start, nav, today_str, (extra_points or {}).get(asset_id))
            peak[asset_id] = seed
            updated_ids.append(asset_id)
            logger.info(f"{asset_id}: 設定来高値を初期記録 {seed['value']:,.2f} ({seed['date']})")

        current_peak = peak[asset_id].get("value", 0)

        # 基準日価格（baseline.prices）。基準日は baseline.dates で資産ごとに指定でき、なければ既定の基準日
        baseline_date = baseline_dates.get(asset_id, default_baseline_date)
        baseline_price = baseline_prices.get(asset_id)
        if (
            baseline_date is not None
            and baseline_price is not None
            and start <= baseline_date <= today_str
            and baseline_price > current_peak
        ):
            peak[asset_id] = {"value": baseline_price, "date": baseline_date}
            current_peak = baseline_price
            if asset_id not in updated_ids:
                updated_ids.append(asset_id)
            logger.info(f"{asset_id}: 基準日({baseline_date})価格{baseline_price:,.2f}が記録済み高値を上回るため補正")

        if nav > current_peak:
            peak[asset_id] = {"value": nav, "date": today_str}
            if asset_id not in updated_ids:
                updated_ids.append(asset_id)
            logger.info(f"{asset_id}: 設定来高値を更新 {current_peak} → {nav} ({today_str})")

    return peak, updated_ids


def seed_initial_peak(
    history: list[dict],
    fund_id: str,
    peak_start_date: str,
    today_nav: float,
    today_str: str,
    extra_points: list[tuple[str, float]] | None = None,
) -> dict:
    """
    ピーク未記録時の初期値を決定する。
    起点日以降で既に記録済みの値（history と extra_points）の中に本日の値より高いものがあれば、
    それを初期ピークとして採用する（取りこぼし防止）。
    """
    best_value = today_nav
    best_date = today_str
    points = [(row.get("date"), row.get(fund_id)) for row in history] + list(extra_points or [])
    for p_date, p_value in points:
        if p_date is None or p_value is None:
            continue
        if p_date < peak_start_date or p_date > today_str:
            continue
        if p_value > best_value:
            best_value = p_value
            best_date = p_date
    return {"value": best_value, "date": best_date}


# ------------------------------------------------------------------
# 下落率・Tier判定・前日比
# ------------------------------------------------------------------

def calc_drawdown(current_nav: float, peak_nav: float) -> float:
    """
    下落率を計算する（%で返す）。
    下落率 = (設定来高値 - 現在値) / 設定来高値 × 100
    現在値が設定来高値以上（高値更新中）の場合は 0.0 を返す（下落率はマイナスになり得ない）。
    """
    if peak_nav <= 0:
        return 0.0
    return max(0.0, (peak_nav - current_nav) / peak_nav * 100)


def judge_tier(drawdown: float, thresholds: list[float]) -> int:
    """
    下落率からTier番号を判定する。thresholds が空（Tier未設定）なら常に 0。

    Args:
        drawdown: 下落率（%、正の値）
        thresholds: [Tier1閾値, Tier2閾値, Tier3閾値]（例: [15, 25, 35]）

    Returns:
        到達最大Tier番号 (0=未到達, 1, 2, 3)
    """
    tier = 0
    for i, threshold in enumerate(thresholds or [], start=1):
        if drawdown >= threshold:
            tier = i
    return tier


def calc_day_change(rows: list[dict], asset_id: str, price: float | None, today_str: str) -> float | None:
    """
    前日比（%）。現在値 price と、直前の（値が異なる）記録との差。
    今日の日付の行は無視する（同日の再実行でも同じ結果にするため）。祝日などで前日と同じ値が続く行は
    飛ばして、値が変わった直前の記録と比べる（休場日の「0.0%」を避ける）。比べる記録がなければ None。
    """
    if price is None:
        return None
    for row in reversed(rows):
        if row.get("date") == today_str:
            continue
        prev = row.get(asset_id)
        if prev is None or prev == price:
            continue
        if prev <= 0:
            return None
        return (price - prev) / prev * 100
    return None


def format_drawdown(drawdown: float, decimals: int = 1) -> str:
    """
    下落率を表示用文字列に整形する。
    calc_drawdown() は0未満を返さないため、0の場合は符号なし（"0.0%"）で表示し、
    "-0.0%" のような紛らわしい表記を避ける。
    """
    if drawdown <= 0:
        return f"{drawdown:.{decimals}f}%"
    return f"-{drawdown:.{decimals}f}%"


# ------------------------------------------------------------------
# 通知済みTier（重複通知防止・回復後の再武装）
# ------------------------------------------------------------------

def load_triggered() -> dict:
    """
    通知済みTierを読み込む。ファイルがなければ空を返す。

    形式: {資産ID: {"tier": 通知済みの最大Tier, "date": 直近の通知日, "notified": {"Tier番号": 通知日},
                    "migrated": {"from", "date"}（旧形式から初期化した記録。任意）}}
    `notified` が通知済み（まだ再武装されていない）Tierの正本。無い記録は旧形式（{"tier","date"}）で、
    update_alert_state() が初回に現在の下落率から初期化する（ここでは変換しない）。
    さらに古い形式（Tier別レコードのリスト [{"tier","date","invested",...}] や整数のリスト）は、
    最大のTierとその日付の旧形式に変換する（invested・note・recovered は破棄。「通知済み」と
    「投資したか」を分け、投資の事実は約定履歴で確認する運用に変えたため）。
    """
    if not TRIGGERED_FILE.exists():
        return {}
    with open(TRIGGERED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    migrated = {}
    for asset_id, value in data.items():
        if isinstance(value, dict):
            if not value.get("tier"):
                continue
            record = {"tier": int(value["tier"]), "date": value.get("date")}
            if isinstance(value.get("notified"), dict):
                record["notified"] = {str(k): v for k, v in value["notified"].items()}
            if value.get("migrated"):
                record["migrated"] = value["migrated"]
            migrated[asset_id] = record
            continue
        records = [{"tier": r, "date": None} if isinstance(r, int) else r for r in (value or [])]
        if records:
            top = max(records, key=lambda r: r["tier"])
            migrated[asset_id] = {"tier": int(top["tier"]), "date": top.get("date")}
    return migrated


def save_triggered(triggered: dict) -> None:
    """通知済みTierを保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(TRIGGERED_FILE, "w", encoding="utf-8") as f:
        json.dump(triggered, f, ensure_ascii=False, indent=2)


def _notified_map(record: dict | None) -> dict[int, str | None]:
    """記録から {通知済みTier番号: 通知日} を取り出す（notified が無い旧形式は空。初期化は update_alert_state が行う）。"""
    return {int(k): v for k, v in ((record or {}).get("notified") or {}).items()}


def notified_tier(asset_id: str, triggered: dict) -> int:
    """通知済み（再武装されていない）Tierのうち最大のもの（なければ0）。旧形式の記録は tier の値。"""
    record = triggered.get(asset_id) or {}
    notified = _notified_map(record)
    if notified:
        return max(notified)
    return int(record.get("tier", 0))


def _store_notified(asset_id: str, notified: dict[int, str | None], triggered: dict, migrated: dict | None = None) -> None:
    """通知済みTierの状態を triggered に書き戻す（通知済みが無ければ記録ごと削除）。"""
    if not notified:
        triggered.pop(asset_id, None)
        return
    top = max(notified)
    dates = [d for d in notified.values() if d]
    triggered[asset_id] = {
        "tier": top,
        "date": max(dates) if dates else None,
        "notified": {str(k): notified[k] for k in sorted(notified)},
    }
    if migrated:
        triggered[asset_id]["migrated"] = migrated


def update_alert_state(
    asset_id: str,
    drawdown: float,
    thresholds: list[float],
    triggered: dict,
    ratio: float = DEFAULT_REARM_RATIO,
    today: str | None = None,
) -> int:
    """
    1資産の通知状態を更新し、今回通知すべきTier（なければ0）を返す（triggered を破壊的に更新）。
    Tier1〜3は互いに独立して「武装中／通知済み」を持つ（校正の前提と同じ定義）。

    1. 再武装: 通知済みのTier i は、下落率が thresholds[i-1] × ratio より浅くなったら武装中に戻す
       （しきい値付近の往復では解除しない。例: Tier1が11%、ratio=0.5 なら5.5%より浅くなるまで通知済み）。
    2. 到達: 下落率がしきい値以上で、武装中のTierを「新規到達」とする。
    3. 新規到達したTierは全て通知済みとして記録し、通知するのは最も深いTierだけ（同時に複数越えても1通）。

    旧形式の記録（notified が無い）は、初回に現在の下落率から初期化する: 旧tier以下のTierのうち、
    まだ再武装されていない（下落率 ≥ しきい値×ratio）ものだけを通知済みにし、"migrated" に効力日を残す。
    切替時点での通知の有無は、旧ロジックと変わらない。
    """
    thresholds = list(thresholds or [])
    if not thresholds:
        return 0
    record = triggered.get(asset_id)
    migrated = (record or {}).get("migrated")
    if record is not None and "notified" not in record:
        legacy_tier = int(record.get("tier", 0))
        notified = {
            i: record.get("date")
            for i in range(1, min(legacy_tier, len(thresholds)) + 1)
            if drawdown >= thresholds[i - 1] * ratio
        }
        migrated = {"from": "v1", "date": today}
        logger.info(f"{asset_id}: 通知状態を旧形式から初期化しました（旧Tier{legacy_tier} → 通知済み {sorted(notified)}、効力日 {today}）")
    else:
        notified = _notified_map(record)

    for i in list(notified):
        if i > len(thresholds) or drawdown < thresholds[i - 1] * ratio:
            del notified[i]

    newly = [i for i, t in enumerate(thresholds, start=1) if drawdown >= t and i not in notified]
    for i in newly:
        notified[i] = today
    _store_notified(asset_id, notified, triggered, migrated)
    return max(newly) if newly else 0


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
# スタンドアローン実行（テスト用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)

    print("=== 下落率・Tier判定テスト（仮の値） ===")
    test_navs = {"sp500": 40000, "orkan": 35000, "tracers": 14000, "sox": 40000}
    test_peak = {"sp500": 45000, "orkan": 38000, "tracers": 17000, "sox": 50000}
    for fund in settings["funds"]:
        fid = fund["id"]
        nav, peak_val = test_navs.get(fid), test_peak.get(fid)
        if nav is None or peak_val is None:
            continue
        dd = calc_drawdown(nav, peak_val)
        tiers = fund.get("tiers") or []
        print(f"  {fund['short_name']:10s}: 下落率={dd:.2f}% → Tier{judge_tier(dd, tiers)} (閾値: {tiers}%)")
