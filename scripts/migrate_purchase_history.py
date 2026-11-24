"""
migrate_purchase_history.py
===========================
data/purchase_history.json を、SBI証券の約定履歴CSV（一次情報）に合わせて整備する一度きりの移行スクリプト。

    python scripts/migrate_purchase_history.py <投信CSV> <米国株CSV> [--expect '{"nasdaq100": 49351}'] [--apply]

既定は dry-run（書き込まない）。--apply を付けたときだけ書き込む（利用者の承認後に実行すること）。

行うこと:
  1. 「既存残高」（6/30時点の口数・取得額の一括記録）が2026年の約定を含んでいる分を取り除く。
     既存残高の口数からCSVの約定（既存残高の日付以前）の合計を引いた残りだけを
     「既存残高（2025年以前）」として残し、2026年の約定はCSVの個別行として持つ
     （従来は6/10の約定が既存残高と個別行の両方に入って二重計上されていた）。
  2. 口数・単価・NISA枠（frame）が無い実績をCSVから補完し、口座区分（account）・汎用の区分を
     CSVと settings.json（monthly_schedule.card）から決め直す。
  3. CSVにあってJSONに無い約定（2026年1〜5月分、NVDA・TSLA）を追加する。
  --expect を渡すと、移行後の保有口数が期待値（SBIの保有画面の口数。銘柄IDごとの合計）と一致するか検査する。
CSVは個人の取引明細のため、公開リポジトリにコミットしないこと。
"""

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from import_sbi_history import (  # noqa: E402
    PURCHASE_FILE,
    SETTINGS_PATH,
    build_records,
    format_record,
    merge_into,
    read_csv_table,
)
from purchase_history import asset_holdings  # noqa: E402


def split_existing_balances(history: dict, csv_records: list[tuple[str, dict]]) -> list[str]:
    """「既存残高」から、CSVに個別行として存在する2026年の約定分を取り除く（history を直接更新）。"""
    notes = []
    for asset_id, bucket in history.items():
        for rec in list(bucket):
            if rec.get("category") != "既存残高":
                continue
            rows = [r for a, r in csv_records if a == asset_id and r["date"] <= rec["date"]]
            units = sum(r.get("units", 0) for r in rows)
            amount = sum(r["amount"] for r in rows)
            rem_units = rec["units"] - units
            rem_amount = rec["amount"] - amount
            bucket.remove(rec)
            if rem_units >= 1:
                bucket.append({
                    "date": "2025-12-31",
                    "category": "既存残高（2025年以前）",
                    "amount": rem_amount,
                    "units": rem_units,
                    "account": rec.get("account"),
                })
                notes.append(
                    f"{asset_id}: 既存残高 {rec['units']:,}口/{rec['amount']:,}円 − CSVの{len(rows)}件（{units:,}口/{amount:,}円） "
                    f"→ 2025年以前 {rem_units:,}口/{rem_amount:,}円"
                )
            else:
                notes.append(
                    f"{asset_id}: 既存残高 {rec['units']:,}口 はCSVの{len(rows)}件（{units:,}口）と一致 → 既存残高の行を削除"
                )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="purchase_history.json をSBI約定履歴CSVに合わせて整備する（既定はdry-run）")
    parser.add_argument("csv", type=Path, nargs="+", help="SBI証券の約定履歴CSV（投信・米国株）")
    parser.add_argument("--expect", type=str, default=None, help='期待する保有口数（JSON。例: {"nasdaq100": 49351}）')
    parser.add_argument("--apply", action="store_true", help="書き込む（既定はdry-run）")
    args = parser.parse_args()

    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    cfg = settings["sbi_import"]
    card_schedule = settings["monthly_schedule"]["card"]
    old = json.loads(PURCHASE_FILE.read_text(encoding="utf-8"))
    history = copy.deepcopy(old)

    csv_records, warnings = [], []
    for path in args.csv:
        kind, rows = read_csv_table(path)
        recs, unmapped, warn = build_records(rows, kind, cfg, card_schedule)
        print(f"{path.name}: {len(rows)}行 → {len(recs)}件" + (f"（対象外: {sorted(set(unmapped))}）" if unmapped else ""))
        csv_records += recs
        warnings += warn

    print("\n=== 1. 既存残高の整理 ===")
    for note in split_existing_balances(history, csv_records):
        print("  " + note)

    result = merge_into(history, csv_records, reclassify=True)
    print(f"\n=== 2・3. CSVとの照合 ===\n既存と一致 {result['duplicates']}件（うち補完・更新 {result['enriched']}件） / 追加 {len(result['added'])}件")
    for asset_id, rec in result["added"]:
        print(format_record(asset_id, rec))
    for w in warnings:
        print(f"  ⚠ {w}")

    for bucket in history.values():
        bucket.sort(key=lambda r: r.get("date", ""))

    print("\n=== 移行後の保有口数（口座区分別） ===")
    ok = True
    expect = json.loads(args.expect) if args.expect else {}
    for asset_id, bucket in history.items():
        h = asset_holdings(bucket)
        by = ", ".join(f"{a}={v['units']:,.0f}" for a, v in sorted(h["by_account"].items()))
        line = f"  {asset_id:14s} 合計 {h['total_units']:>10,.0f}  ({by})"
        if asset_id in expect:
            diff = h["total_units"] - expect[asset_id]
            mark = "OK" if abs(diff) <= 3 else "NG"
            ok &= mark == "OK"
            line += f"  期待 {expect[asset_id]:,} 差 {diff:+,.0f} {mark}"
        print(line)

    if not ok:
        print("\n期待値と一致しない銘柄があります。書き込みを中止します。")
        return 1
    if args.apply:
        PURCHASE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n{PURCHASE_FILE} に書き込みました")
    else:
        print("\n[dry-run] 書き込みは行っていません（--apply で書き込みます）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
