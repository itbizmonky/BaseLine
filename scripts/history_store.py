"""
history_store.py
================
日次価格の履歴CSV（history.csv・positions_history.csv）を「基準日キー」で更新する共通処理。

行の日付は価格の基準日（as_of。投信＝基準価額の基準日、米国株＝取引日）。取得日（実行日）ではない。
朝の実行は前営業日の確定値を取得するため、取得日をキーにすると約定マーカーとの日付がずれ、祝日には
同値の架空の行が増えていた。基準日をキーにすれば、祝日・未更新の日は同じ行への同値の書き込みになり、
行は増えない（べき等）。

- 同じ基準日・同じ列に既存の値があり、値が異なる場合は上書きせず、data/history_corrections.csv に記録する
  （再取得で値が変わった異常を見逃さないため）。
- 空欄のセルは補完する。列が増える場合は既存の行を空欄で補完する。行は日付順に保つ。
"""

import csv
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CORRECTIONS_FILE = DATA_DIR / "history_corrections.csv"
CORRECTIONS_HEADER = ["as_of", "column", "recorded", "refetched", "fetched_on", "file"]


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    """CSVを (ヘッダ, 行（文字列のdict）) で返す。ファイルがなければ (['date'], [])。"""
    if not Path(path).exists():
        return ["date"], []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or ["date"]), list(reader)


def write_rows(path: Path, header: list[str], rows: list[dict]) -> None:
    """日付順に書き出す（既存ファイルと同じCRLF）。"""
    Path(path).parent.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["date"]))


def log_correction(as_of: str, column: str, recorded: str, refetched: str, fetched_on: str | None,
                   source: Path, corrections_path: Path | None = None) -> None:
    """既存の値と再取得の値が異なる場合の記録（上書きしない）。"""
    corrections_path = Path(corrections_path or CORRECTIONS_FILE)
    new_file = not corrections_path.exists()
    corrections_path.parent.mkdir(exist_ok=True)
    with open(corrections_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(CORRECTIONS_HEADER)
        writer.writerow([as_of, column, recorded, refetched, fetched_on or "", Path(source).name])
    logger.warning(f"{Path(source).name} {as_of} {column}: 既存値{recorded}と再取得値{refetched}が異なる（上書きせず訂正記録に残しました）")


def upsert_rows(
    path: Path,
    rows: dict[str, dict[str, str]],
    fetched_on: str | None = None,
    fill_only: tuple[str, ...] = (),
    corrections_path: Path | None = None,
) -> dict:
    """
    rows（{基準日 'YYYY-MM-DD': {列名: 値の文字列}}）を path のCSVへ反映する。
    fill_only の列は、空欄のときだけ入れ、既存の値と異なっても訂正記録を残さない（為替など補助的な列）。
    Returns: {"added": [基準日], "filled": [(基準日, 列)], "conflicts": [(基準日, 列)]}
    """
    header, existing = read_rows(path)
    by_date = {r["date"]: r for r in existing}
    result = {"added": [], "filled": [], "conflicts": []}
    changed = not Path(path).exists()

    for as_of in sorted(rows):
        values = {c: v for c, v in rows[as_of].items() if v not in (None, "")}
        if not values:
            continue
        for column in values:
            if column not in header:
                header.append(column)
                changed = True
        row = by_date.get(as_of)
        if row is None:
            row = {"date": as_of}
            by_date[as_of] = row
            existing.append(row)
            result["added"].append(as_of)
            changed = True
        for column, value in values.items():
            current = row.get(column) or ""
            if current == "":
                row[column] = value
                result["filled"].append((as_of, column))
                changed = True
            elif current != value and column not in fill_only:
                result["conflicts"].append((as_of, column))
                log_correction(as_of, column, current, value, fetched_on, path, corrections_path)

    if changed:
        write_rows(path, header, existing)
        logger.info(f"{Path(path).name}: 追加{len(result['added'])}行・補完{len(result['filled'])}セル")
    else:
        logger.info(f"{Path(path).name}: 変更なし（同じ基準日の同値）")
    return result


def as_of_key(as_of: date | None, fallback: date) -> str:
    """基準日のキー文字列。基準日が不明（None）の場合は fallback（直近の平日）で代用する。"""
    return (as_of or fallback).isoformat()
