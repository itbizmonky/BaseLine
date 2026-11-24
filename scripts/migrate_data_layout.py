"""
migrate_data_layout.py
======================
data/ のファイル構成を、全保有資産を統一して扱う新しい構造に移す一度きりの移行スクリプト。

    python scripts/migrate_data_layout.py [--root <リポジトリのルート>] [--apply]

既定は dry-run（書き込まない）。--apply を付けたときだけ書き込む（利用者の承認後に実行すること）。
--root は作業コピーでの動作確認用（既定はこのリポジトリ）。

行うこと:
  1. history.csv に、保有ポジションから移した2列（sbi_v_sp500・newnisa_orkan）を追加する。値は
     positions_history.csv の同じ日付の値（2026-08-07〜）。それ以前は空欄。
  2. positions_history.csv を、米国株の列（tesla・nvda）と usdjpy（その日のレート）の構成に作り直す。
     行の日付は history.csv の実行日（2026-08-07以降）。値はその日の朝時点の米国株の前営業日終値で、
     既存の値は保持し、欠けている値（TSLAの10/21以降・NVDA・USDJPY）だけを Yahoo Finance のチャートから補う
     （NVDAは最初の約定日 2026-11-12 以降）。
  3. positions.json から、投資信託に移した資産（newnisa_orkan・sbi_v_sp500・nasdaq100）を取り除き、
     tesla・nvda の最新値を入れる。
  4. triggered.json を新形式（{資産ID: {"tier", "date"}}、alert対象のみ）に移す。
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import positions  # noqa: E402
from assets import all_assets, stock_assets  # noqa: E402

STOCK_FIRST_DATES = {"tesla": "2026-08-07", "nvda": "2026-11-12"}  # 最高値の起点（最初の約定日）
POSITIONS_START = "2026-08-07"
MOVED_TO_FUNDS = ["sbi_v_sp500", "newnisa_orkan"]


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def prev_close(closes: list[tuple[str, float]], row_date: str) -> float | None:
    """row_date（JST朝の実行日）より前で最新の終値（米国の前営業日の終値）。"""
    candidates = [c for d, c in closes if d < row_date]
    return candidates[-1] if candidates else None


def same_day_rate(rates: list[tuple[str, float]], row_date: str) -> float | None:
    """row_date 当日（なければそれ以前で最新）のレート。"""
    candidates = [c for d, c in rates if d <= row_date]
    return candidates[-1] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="data/ を新しい構成に移す（既定はdry-run）")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data = args.root / "data"
    settings = json.loads((args.root / "config" / "settings.json").read_text(encoding="utf-8"))
    alert_ids = [a["id"] for a in all_assets(settings) if a["alert"]]

    # ---- 1. history.csv
    h_fields, h_rows = read_csv(data / "history.csv")
    p_fields, p_rows = read_csv(data / "positions_history.csv")
    old_pos = {r["date"]: r for r in p_rows}
    for col in MOVED_TO_FUNDS:
        if col not in h_fields:
            h_fields.append(col)
    moved = 0
    for r in h_rows:
        for col in MOVED_TO_FUNDS:
            val = (old_pos.get(r["date"]) or {}).get(col, "")
            if val and not r.get(col):
                r[col] = f"{float(val):.0f}"
                moved += 1
    print(f"1. history.csv: 列を追加 {MOVED_TO_FUNDS}、positions_history.csv から {moved} セルを移動")

    # ---- 2. positions_history.csv（米国株とusdjpy）
    tickers = {a["id"]: a["ticker"] for a in stock_assets(settings)}
    closes = {sid: positions.fetch_daily_closes(tickers[sid], "6mo") for sid in STOCK_FIRST_DATES}
    rates = positions.fetch_daily_closes(positions.USDJPY_SYMBOL, "6mo")
    for name, series in {**closes, "usdjpy": rates}.items():
        print(f"   Yahoo Finance {name}: {len(series)}日分" + ("" if series else "（取得できませんでした。既存の値のみで作り直します）"))
    new_fields = ["date", "tesla", "nvda", "usdjpy"]
    new_rows, filled = [], {"tesla": 0, "nvda": 0, "usdjpy": 0}
    dates = sorted({r["date"] for r in h_rows if r["date"] >= POSITIONS_START} | {d for d in old_pos if d >= POSITIONS_START})
    for d in dates:
        old = old_pos.get(d, {})
        row = {"date": d, "tesla": old.get("tesla", ""), "nvda": old.get("nvda", ""), "usdjpy": old.get("usdjpy", "")}
        for sid, first in STOCK_FIRST_DATES.items():
            if not row[sid] and d >= first:
                v = prev_close(closes[sid], d)
                if v is not None:
                    row[sid] = f"{v:.2f}"
                    filled[sid] += 1
        if not row["usdjpy"]:
            v = same_day_rate(rates, d)
            if v is not None:
                row["usdjpy"] = f"{v:.2f}"
                filled["usdjpy"] += 1
        new_rows.append(row)
    print(f"2. positions_history.csv: {len(new_rows)}行に作り直し（補完: {filled}）")

    # ---- 3. positions.json
    pos_path = data / "positions.json"
    positions_json = json.loads(pos_path.read_text(encoding="utf-8")) if pos_path.exists() else {}
    for gone in ("newnisa_orkan", "sbi_v_sp500", "nasdaq100"):
        positions_json.pop(gone, None)
    for sid in STOCK_FIRST_DATES:
        last = next(((r["date"], float(r[sid])) for r in reversed(new_rows) if r[sid]), None)
        if last and (sid not in positions_json or positions_json[sid]["date"] < last[0]):
            positions_json[sid] = {"value": last[1], "date": last[0]}
    print(f"3. positions.json: {json.dumps(positions_json, ensure_ascii=False)}")

    # ---- 4. triggered.json
    triggered_path = data / "triggered.json"
    raw =json.loads(triggered_path.read_text(encoding="utf-8")) if triggered_path.exists() else {}
    migrated = {}
    for aid, value in raw.items():
        if aid not in alert_ids:
            continue
        if isinstance(value, dict):
            if value.get("tier"):
                migrated[aid] = {"tier": value["tier"], "date": value.get("date")}
            continue
        records = [{"tier": r, "date": None} if isinstance(r, int) else r for r in value]
        if records:
            top = max(records, key=lambda r: r["tier"])
            migrated[aid] = {"tier": top["tier"], "date": top.get("date")}
    print(f"4. triggered.json: {json.dumps(migrated, ensure_ascii=False)}（alert対象 {alert_ids} のみ）")

    if not args.apply:
        print("\n[dry-run] 書き込みは行っていません（--apply で書き込みます）")
        return 0
    write_csv(data / "history.csv", h_fields, h_rows)
    write_csv(data / "positions_history.csv", new_fields, new_rows)
    pos_path.write_text(json.dumps(positions_json, ensure_ascii=False, indent=2), encoding="utf-8")
    triggered_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n書き込みました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
