"""
migrate_history_asof.py
=======================
history.csv・positions_history.csv・peak.json の日付を、「取得日（実行日）」から「価格の基準日」へ付け替える
一度きりの移行スクリプト（v2.0パッチの migrate_history_asof.py を現行の構成に合わせて書き直したもの）。

    python scripts/migrate_history_asof.py [--root <リポジトリのルート>] [--apply]

既定は dry-run（書き込まない）。--apply を付けたときだけ書き込む（利用者の承認後に実行すること）。
値の不一致（同じ基準日になる行で値が異なる）がある場合は --apply を拒否する。

背景: 朝7時の実行は前営業日の確定値を取得しているが、行の日付は実行日で記録されていた。そのため推移グラフで
約定マーカーが価格の線より1営業日左にずれ、祝日には同値の架空の行が残っていた。

規則:
  history.csv（投信）       : 実行日 D の行 → D の直前の日本の営業日（2026年の祝日表をこのスクリプト内に持つ）
                              手動で追加した 2026-07-07 の行は変更しない。全列が空の行は削除。
  positions_history.csv     : 実行日 D の行 → D より前で最新の米国の取引日（Yahoo Financeの日次終値の日付から決める）
  peak.json                 : 最高値の日付が履歴の旧日付と一致し、かつ値が同じものだけ、新しい基準日へ付け替え
                              （約定単価・基準日価格など履歴由来でない最高値は日付を変えない）
  同じ基準日になる行は、値が一致すれば1行に統合する。
検証: 付け替え前後の history.csv を、purchase_history.json の約定単価（約定日の基準価額）と突合して一致率を出す。
"""

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import history_store  # noqa: E402

# 日本の祝日（投信の基準価額が算出されない日）。履歴の期間（2026-07〜）のうち、土日以外のもの。年末年始は含まない
JP_HOLIDAYS_2026 = {
    "2026-07-20", "2026-08-11", "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12", "2026-11-03", "2026-11-23",
}
MANUAL_ROWS = {"2026-07-07"}   # settings.json の baseline.prices（7/7の実績値）から手動で追加した行


def prev_jp_business_day(d: date, holidays: set[str] = JP_HOLIDAYS_2026) -> date:
    """d より前の直近の日本の営業日。"""
    x = d - timedelta(days=1)
    while x.weekday() >= 5 or x.isoformat() in holidays:
        x -= timedelta(days=1)
    return x


def prev_trading_day(d: date, trading_days: list[str]) -> str | None:
    """d より前で最新の取引日（YYYY-MM-DD）。なければ None。"""
    earlier = [t for t in trading_days if t < d.isoformat()]
    return max(earlier) if earlier else None


def _merge(new_rows: dict[str, dict], key: str, row: dict, columns: list[str], source_date: str, conflicts: list, fill_only=()):
    """同じ基準日に付け替わる行を1行にまとめる。値が異なる場合は conflicts に記録（先の値を残す）。"""
    target = new_rows.setdefault(key, {"date": key})
    for c in columns:
        v = (row.get(c) or "").strip()
        if not v:
            continue
        cur = target.get(c) or ""
        if cur == "":
            target[c] = v
        elif cur != v and c not in fill_only:
            conflicts.append({"as_of": key, "column": c, "kept": cur, "other": v, "source_date": source_date})


def relabel_fund_rows(rows: list[dict], columns: list[str], holidays: set[str] = JP_HOLIDAYS_2026) -> tuple[list[dict], list[dict], list[dict]]:
    """投信の履歴を基準日へ付け替える。Returns: (新しい行, 付け替えのログ, 不一致)"""
    new_rows, log, conflicts = {}, [], []
    for row in rows:
        d = row["date"]
        if d in MANUAL_ROWS:
            _merge(new_rows, d, row, columns, d, conflicts)
            log.append({"old": d, "new": d, "action": "keep", "reason": "手動追加行（基準日＝実日付）"})
        elif all(not (row.get(c) or "").strip() for c in columns):
            log.append({"old": d, "new": "", "action": "drop_empty", "reason": "全銘柄が空欄"})
        else:
            new = prev_jp_business_day(date.fromisoformat(d), holidays).isoformat()
            action = "merge" if new in new_rows else "relabel"
            _merge(new_rows, new, row, columns, d, conflicts)
            log.append({"old": d, "new": new, "action": action, "reason": "実行日→直前の営業日" if action == "relabel" else "同じ基準日の行へ統合"})
    return sorted(new_rows.values(), key=lambda r: r["date"]), log, conflicts


def relabel_stock_rows(rows: list[dict], columns: list[str], trading_days: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """米国株の履歴を取引日へ付け替える（usdjpy は先に入っている値を残す）。"""
    new_rows, log, conflicts = {}, [], []
    for row in rows:
        d = row["date"]
        new = prev_trading_day(date.fromisoformat(d), trading_days)
        if new is None:
            log.append({"old": d, "new": "", "action": "unresolved", "reason": "対応する取引日がない"})
            conflicts.append({"as_of": d, "column": "date", "kept": "", "other": "", "source_date": d})
            continue
        action = "merge" if new in new_rows else "relabel"
        _merge(new_rows, new, row, columns, d, conflicts, fill_only=("usdjpy",))
        log.append({"old": d, "new": new, "action": action, "reason": "実行日→直前の取引日" if action == "relabel" else "同じ取引日の行へ統合"})
    return sorted(new_rows.values(), key=lambda r: r["date"]), log, conflicts


def relabel_peak(peak: dict, fund_log: list[dict], fund_rows_old: list[dict], stock_log: list[dict], stock_rows_old: list[dict]) -> tuple[dict, list[dict]]:
    """最高値の日付を付け替える。履歴の旧日付・値が一致するものだけ。Returns: (新しい peak, 変更一覧)"""
    def mapping(log):
        return {x["old"]: x["new"] for x in log if x["new"]}
    sources = [(fund_rows_old, mapping(fund_log)), (stock_rows_old, mapping(stock_log))]
    out, changes = {}, []
    for aid, rec in peak.items():
        new_rec = dict(rec)
        for old_rows, mp in sources:
            hit = next((r for r in old_rows if r["date"] == rec.get("date") and _num(r.get(aid)) == rec.get("value")), None)
            if hit and mp.get(hit["date"]) and mp[hit["date"]] != rec["date"]:
                new_rec["date"] = mp[hit["date"]]
                changes.append({"asset": aid, "value": rec["value"], "old": rec["date"], "new": new_rec["date"]})
                break
        out[aid] = new_rec
    return out, changes


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def match_purchases(rows: list[dict], purchases: dict, fund_ids: list[str]) -> tuple[int, int, list[str]]:
    """投信の約定単価（約定日の基準価額）が、履歴の同じ日付の行の値と一致する件数。Returns: (一致, 対象, 不一致の一覧)"""
    by_date = {r["date"]: r for r in rows}
    matched, total, misses = 0, 0, []
    for aid in fund_ids:
        for rec in purchases.get(aid, []):
            if not rec.get("price") or rec.get("currency", "JPY") != "JPY":
                continue
            row = by_date.get(rec["date"])
            value = _num((row or {}).get(aid))
            if value is None:
                continue   # その日の履歴が無い（履歴の期間外・欠測）ものは対象外
            total += 1
            if abs(value - float(rec["price"])) < 0.5:
                matched += 1
            else:
                misses.append(f"{aid} {rec['date']} 約定{rec['price']} / 履歴{value:.0f}")
    return matched, total, misses


def us_trading_days() -> list[str]:
    """Yahoo Financeの日次終値（TSLA）から、米国の取引日の一覧を得る。"""
    import positions
    closes = positions.fetch_daily_closes("TSLA", "2y")
    return [d for d, _ in closes]


def run(root: Path, apply: bool, trading_days: list[str] | None = None) -> int:
    data = root / "data"
    h_header, h_rows = history_store.read_rows(data / "history.csv")
    p_header, p_rows = history_store.read_rows(data / "positions_history.csv")
    peak = json.loads((data / "peak.json").read_text(encoding="utf-8"))
    purchases = json.loads((data / "purchase_history.json").read_text(encoding="utf-8"))
    fund_cols = [c for c in h_header if c != "date"]
    stock_cols = [c for c in p_header if c != "date"]
    trading_days = trading_days if trading_days is not None else us_trading_days()

    new_h, h_log, h_conf = relabel_fund_rows(h_rows, fund_cols)
    new_p, p_log, p_conf = relabel_stock_rows(p_rows, stock_cols, trading_days)
    new_peak, peak_changes = relabel_peak(peak, h_log, h_rows, p_log, p_rows)

    before = match_purchases(h_rows, purchases, fund_cols)
    after = match_purchases(new_h, purchases, fund_cols)
    count = lambda log, a: sum(1 for x in log if x["action"] == a)
    print(f"history.csv           : {len(h_rows)}行 → {len(new_h)}行（付け替え {count(h_log, 'relabel')}／統合 {count(h_log, 'merge')}／空行の削除 {count(h_log, 'drop_empty')}／保持 {count(h_log, 'keep')}）")
    print(f"positions_history.csv : {len(p_rows)}行 → {len(new_p)}行（付け替え {count(p_log, 'relabel')}／統合 {count(p_log, 'merge')}）")
    print(f"約定単価との一致（投信）: 付け替え前 {before[0]}/{before[1]} → 付け替え後 {after[0]}/{after[1]}")
    for miss in after[2]:
        print(f"  不一致: {miss}")
    print(f"peak.json の日付の付け替え: {len(peak_changes)}件")
    for c in peak_changes:
        print(f"  {c['asset']:14s} {c['value']:>10,.2f}  {c['old']} → {c['new']}")
    conflicts = h_conf + p_conf
    for c in conflicts:
        print(f"  ※値の不一致: {c}")

    if conflicts:
        print("値の不一致があるため、--apply は実行できません。")
        return 1
    if not apply:
        print("\n（dry-run: ファイルは変更していません。--apply で反映します）")
        return 0
    history_store.write_rows(data / "history.csv", h_header, new_h)
    history_store.write_rows(data / "positions_history.csv", p_header, new_p)
    (data / "peak.json").write_text(json.dumps(new_peak, ensure_ascii=False, indent=2), encoding="utf-8")
    corrections = data / "history_corrections.csv"
    if not corrections.exists():
        with open(corrections, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(history_store.CORRECTIONS_HEADER)
    print("\n反映しました（history.csv・positions_history.csv・peak.json。history_corrections.csv は未作成なら作成）。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="履歴・最高値の日付を、実行日から価格の基準日へ付け替える（一度きり）")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent, help="リポジトリのルート（作業コピーでの確認用）")
    parser.add_argument("--apply", action="store_true", help="書き込む（利用者の承認後）")
    args = parser.parse_args()
    sys.exit(run(args.root, args.apply))
