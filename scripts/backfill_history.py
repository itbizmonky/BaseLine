"""
backfill_history.py
===================
履歴（history.csv・positions_history.csv）の欠けている期間を、外部の日次価格で補う一度きりのスクリプト。
推移グラフを、全銘柄で履歴の開始日（settings.json の history.start_date。既定 2026-07-07）から表示するため。

    python scripts/backfill_history.py [--root <リポジトリのルート>] [--apply]

既定は dry-run（書き込まない）。--apply を付けたときだけ書き込む（利用者の承認後に実行すること）。

取得元:
  投資信託 … 日経の基準価額チャートが使う日次データ（QUICK。https://ot36.qhit.net/nikkei/navchart/FundData.aspx）。
             日付は基準価額の基準日で、history.csv の基準日キーと同じ意味。
  米国株   … Yahoo Finance のチャートの日次終値（TSLA・NVDA）。日付は取引日。usdjpy は JPY=X の、取引日の翌日の値（既存の usdjpy＝取引日の翌朝に取得したレート、と同じ意味にそろえる）。
補う規則:
  - 既存のセルは**上書きしない**。空欄のセルと、まだ無い日付の行だけを補う。既存の値と異なる値があれば報告する
    （既存の履歴との一致を確認するため。--apply はこの不一致があっても実行できるが、既存の値を優先する）。
  - 補う期間は history.start_date（2026-01-01）から、各ファイルの最後の日付まで。
  - peak.json: 資産ごとの起点日（baseline.dates。なければ peak_start_date）以降の最高値を、取得した日次データから計算し、
    記録済みの最高値より高い場合だけ引き上げる（起点日が履歴の範囲より前の資産＝SBI・Vなどの取りこぼしを直すため。下げない・記録の無い資産は作らない）。
"""

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import history_store  # noqa: E402
import positions  # noqa: E402

QUICK_URL = "https://ot36.qhit.net/nikkei/navchart/FundData.aspx"
QUICK_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nikkei.com/nkd/fund/chart/"}
START = "2026-07-07"


def fetch_quick_nav(fund_code: str, months: int = 24) -> dict[str, float]:
    """日経（QUICK）の日次の基準価額 {基準日 'YYYY-MM-DD': 基準価額}。取得できなければ例外。"""
    params = {"fundtype": "J", "fundcode": fund_code, "mode": "D", "termtype": "M", "term": months}
    resp = requests.get(QUICK_URL, params=params, headers=QUICK_HEADERS, timeout=30)
    resp.raise_for_status()
    return {row[0].replace("/", "-"): float(row[1]) for row in resp.json()["funddata"] if row[1] not in ("", None)}


def fetch_closes(symbol: str) -> dict[str, float]:
    """Yahoo Financeの日次終値 {取引日: 終値}（直近2年）。"""
    return dict(positions.fetch_daily_closes(symbol, "2y"))


def next_day_rate(fx: dict[str, float], day: str) -> float | None:
    """
    取引日 day の usdjpy。既存の positions_history.csv の usdjpy は「取引日の翌朝（日本時間）に取得したレート」なので、
    それに合わせて、JPY=X の day より後で最初の日の値を使う（取れなければ None）。
    """
    later = [d for d in fx if d > day]
    return fx[min(later)] if later else None


def build_fill(existing: dict[str, dict], incoming: dict[str, dict], start: str, end: str, fmt) -> tuple[dict, list]:
    """
    incoming（{日付: {列: 値}}）のうち、既存に無いセルだけを返す。
    Returns: ({日付: {列: 文字列}}, 既存の値と異なるもの [(日付, 列, 既存, 取得値)])
    """
    rows, conflicts = {}, []
    for day in sorted(incoming):
        if not (start <= day <= end):
            continue
        for col, value in incoming[day].items():
            text = fmt(col, value)
            current = (existing.get(day) or {}).get(col) or ""
            if current == "":
                rows.setdefault(day, {})[col] = text
            elif current != text:
                conflicts.append((day, col, current, text))
    return rows, conflicts


def raise_peaks(peak: dict, series: dict[str, dict[str, float]], starts: dict[str, str], end: str) -> list[dict]:
    """資産ごとに、起点日〜end の最高値を計算し、記録済みより高ければ peak を引き上げる（下げない）。変更の一覧を返す。"""
    changes = []
    for aid, values in series.items():
        window = [(v, d) for d, v in values.items() if starts[aid] <= d <= end]
        if not window or aid not in peak:   # 記録の無い資産は、通常の初期記録（update_peak）に任せる
            continue
        value, day = max(window)
        current = (peak.get(aid) or {}).get("value", 0)
        if value > current:
            changes.append({"asset": aid, "old": peak.get(aid), "new": {"value": value, "date": day}})
            peak[aid] = {"value": value, "date": day}
    return changes


def run(root: Path, apply: bool, quick: dict | None = None, closes: dict | None = None, fx: dict | None = None) -> int:
    data = root / "data"
    settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
    peak = json.loads((data / "peak.json").read_text(encoding="utf-8"))
    baseline_dates = settings.get("baseline", {}).get("dates", {})
    peak_start = settings.get("peak_start_date", START)
    start = settings.get("history", {}).get("start_date", START)   # 履歴の開始日（settings.json の history.start_date）
    funds = [f["id"] for f in settings["funds"]]
    stocks = [s["id"] for s in settings.get("positions", {}).get("items", [])]
    tickers = {s["id"]: s["ticker"] for s in settings.get("positions", {}).get("items", [])}

    if quick is None:
        quick = {f["id"]: fetch_quick_nav(f["fund_code"]) for f in settings["funds"]}
    if closes is None:
        closes = {sid: fetch_closes(tickers[sid]) for sid in stocks}
    if fx is None:
        fx = fetch_closes(positions.USDJPY_SYMBOL)

    h_header, h_rows = history_store.read_rows(data / "history.csv")
    p_header, p_rows = history_store.read_rows(data / "positions_history.csv")
    h_existing = {r["date"]: r for r in h_rows}
    p_existing = {r["date"]: r for r in p_rows}
    h_end, p_end = max(h_existing), max(p_existing)

    incoming_h = {}
    for fid, series in quick.items():
        for day, value in series.items():
            incoming_h.setdefault(day, {})[fid] = value
    fill_h, conf_h = build_fill(h_existing, incoming_h, start, h_end, lambda c, v: f"{v:.0f}")

    incoming_p = {}
    for sid, series in closes.items():
        for day, value in series.items():
            incoming_p.setdefault(day, {})[sid] = value
            rate = next_day_rate(fx, day)
            if rate is not None:
                incoming_p[day]["usdjpy"] = rate
    fill_p, conf_p = build_fill(p_existing, incoming_p, start, p_end, lambda c, v: f"{v:.2f}")

    # 最高値: 起点日以降の日次データから。約定単価・基準日価格など履歴に無い既知の価格はそのまま（下げない）
    series_for_peak = {**quick, **closes}
    starts = {aid: baseline_dates.get(aid, peak_start) for aid in series_for_peak}
    new_peak = json.loads(json.dumps(peak))
    peak_changes = raise_peaks(new_peak, series_for_peak, starts, max(h_end, p_end))

    def counts(fill, existing):
        cells = sum(len(v) for v in fill.values())
        return len([d for d in fill if d not in existing]), cells

    added_h, cells_h = counts(fill_h, h_existing)
    added_p, cells_p = counts(fill_p, p_existing)
    print(f"history.csv           : 行を{added_h}件追加・セルを{cells_h}個補完（{start}〜{h_end}）")
    per_col = {}
    for d, cols in fill_h.items():
        for c in cols:
            per_col[c] = per_col.get(c, 0) + 1
    print("  列別の補完:", ", ".join(f"{c} {n}" for c, n in per_col.items()))
    print(f"positions_history.csv : 行を{added_p}件追加・セルを{cells_p}個補完（{start}〜{p_end}）")
    print(f"既存の値との不一致      : history.csv {len(conf_h)}件・positions_history.csv {len(conf_p)}件（既存の値を優先）")
    for c in conf_h + [c for c in conf_p if c[1] != "usdjpy"]:
        print(f"  {c}")
    print(f"  （うち usdjpy {sum(1 for c in conf_p if c[1] == 'usdjpy')}件は為替の取得時刻の差による小さな違い。補助的な列で既存の値を優先）")
    print(f"peak.json の引き上げ    : {len(peak_changes)}件")
    for c in peak_changes:
        print(f"  {c['asset']:14s} {c['old']} → {c['new']}")

    if not apply:
        print("\n（dry-run: ファイルは変更していません。--apply で反映します）")
        return 0
    history_store.upsert_rows(data / "history.csv", fill_h)
    history_store.upsert_rows(data / "positions_history.csv", fill_p)
    (data / "peak.json").write_text(json.dumps(new_peak, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n反映しました（history.csv・positions_history.csv・peak.json）。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="履歴の欠けている期間を外部の日次価格で補う（一度きり）")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent, help="リポジトリのルート（作業コピーでの確認用）")
    parser.add_argument("--apply", action="store_true", help="書き込む（利用者の承認後）")
    args = parser.parse_args()
    sys.exit(run(args.root, args.apply))
