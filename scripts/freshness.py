"""
freshness.py
============
価格の鮮度判定（純粋関数）

価格には「基準日（as_of）」がある（投信＝日経ページの基準価格の日付、米国株＝取引日）。
基準日が直近の平日より `max_lag` 平日を超えて古い価格は stale とし、最高値の更新・Tier判定・通知状態の
更新に使わない（古い価格を当日の価格として扱わないため）。祝日・年末年始・GWで基準日が数日遅れるのは
通常の状態なので、祝日カレンダーは持たず、遅れの許容（既定3平日）で代替する。
基準日が取れない（None）場合は unknown とし、価格は使う（日付の表記変更だけでTier通知を止めない）。
"""

import re
from datetime import date, timedelta

DEFAULT_MAX_LAG_WEEKDAYS = 3

STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_UNKNOWN = "unknown"


def prev_weekday(d: date) -> date:
    """d の前日以前で最も近い平日（土日を飛ばす）。"""
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def weekday_lag(as_of: date, today: date) -> int:
    """
    基準日が「直近の平日（today の前営業日相当）」より何平日遅れているか。
    as_of の翌日〜直近の平日 に含まれる平日の数（as_of が直近の平日以降なら0）。
    例: 月曜の実行で基準日が金曜 → 0 / 月曜が祝日で火曜の実行・基準日が金曜 → 1。
    """
    expected = prev_weekday(today)
    lag, d = 0, as_of
    while d < expected:
        d += timedelta(days=1)
        if d.weekday() < 5:
            lag += 1
    return lag


def assess(as_of: date | None, today: date, max_lag: int = DEFAULT_MAX_LAG_WEEKDAYS, forced_stale: bool = False) -> dict:
    """
    価格の鮮度を判定する。
    forced_stale: 取得に失敗して前回値を使う場合（日付に関わらず stale 扱い）。
    Returns: {"as_of": date|None, "lag": int|None, "status": "fresh"|"stale"|"unknown"}
    """
    lag = weekday_lag(as_of, today) if as_of is not None else None
    if forced_stale:
        status = STATUS_STALE
    elif as_of is None:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_STALE if lag > max_lag else STATUS_FRESH
    return {"as_of": as_of, "lag": lag, "status": status}


def parse_nikkei_asof(text: str, today: date) -> date | None:
    """
    日経の投信ページの「基準価格(11/24)： 42,418 円」から基準日を取り出す。
    年は今日の年とし、今日より2日以上未来になる場合は前年（年初に前年12月の基準日が来るとき）。
    """
    m = re.search(r"基準価格\s*[（(]\s*(\d{1,2})/(\d{1,2})\s*[）)]", text or "")
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        candidate = date(today.year, month, day)
        if candidate > today + timedelta(days=1):
            candidate = date(today.year - 1, month, day)
    except ValueError:
        return None
    return candidate


def to_date(value: str | date | None) -> date | None:
    """'YYYY-MM-DD' または date を date にする（不正・None は None）。"""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def short_date(d: date | str | None) -> str:
    """表示用 'M/D'（不明は '日付不明'）。"""
    d = to_date(d)
    return f"{d.month}/{d.day}" if d else "日付不明"


def classify_prices(quotes: dict, today: date, max_lag: int = DEFAULT_MAX_LAG_WEEKDAYS) -> dict:
    """
    資産ごとの価格（{資産ID: {"value", "as_of", "forced_stale"(任意)} | None}）に鮮度判定を付ける。
    Returns: {資産ID: {"value", "as_of", "lag", "status"} | None}（取得できていない資産は None）
    """
    out = {}
    for aid, q in quotes.items():
        if not q or q.get("value") is None:
            out[aid] = None
            continue
        a = assess(to_date(q.get("as_of")), today, max_lag, bool(q.get("forced_stale")))
        out[aid] = {"value": q["value"], **a}
    return out


def usable_values(info: dict) -> dict:
    """判定に使える価格だけ {資産ID: 値}。取得失敗（None）と stale は含めない（最高値更新・Tier判定・履歴追記の入力）。"""
    return {aid: i["value"] for aid, i in info.items() if i and i["status"] != STATUS_STALE}
