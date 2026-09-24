"""
import_sbi_history.py
=====================
SBI証券の約定履歴CSVを data/purchase_history.json に取り込むスクリプト（手動実行）。

    python scripts/import_sbi_history.py <約定履歴CSV> [--include-attack] [--dry-run]

- 預り区分から口座を自動判定する（config/settings.json の sbi_import.account_map）:
    NISA (つみたて) → side（別枠積立） / NISA (成長) → attack（攻撃フェーズ）
- 既定では別枠積立（side）のみ取り込む。攻撃フェーズ（attack）の区分（①分・Tier1・定期積立など）は
  CSVから判定できないため、--include-attack を付けた場合のみ取り込み、区分は「未分類」とする
- 銘柄名は全角→半角に正規化し、sbi_import.fund_keywords のキーワードで銘柄IDに対応付ける
  （対応しない銘柄＝監視対象外ファンドは無視する）
- 同じ（銘柄・約定日・金額・口座）の実績が既にある場合は重複として取り込まない（何度実行しても安全）
- CSVは個人の取引明細のため、公開リポジトリにコミットしないこと
"""

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.json"
PURCHASE_FILE = ROOT / "data" / "purchase_history.json"


def read_csv_rows(path: Path) -> list[list[str]]:
    """SBI証券のCSV（UTF-8/CP932どちらでも可）を読み、約定データ行（ヘッダ行の次以降）を返す。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("CSVの文字コードを判定できませんでした（UTF-8/CP932以外）")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("約定日")), None)
    if start is None:
        raise SystemExit("「約定日」で始まるヘッダ行が見つかりません（SBI証券の約定履歴CSVではない可能性があります）")
    return [r for r in csv.reader(lines[start + 1:]) if len(r) >= 14]


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


def parse_int(s: str) -> int | None:
    try:
        return int(norm(s).replace(",", ""))
    except ValueError:
        return None


def build_records(rows, cfg: dict, include_attack: bool) -> tuple[list[tuple[str, dict]], list[str], int]:
    """CSV行から (銘柄ID, レコード) のリストを作る。戻り値: (実績, 対応しなかった銘柄名, 口座対象外の件数)"""
    account_map = {norm(k): v for k, v in cfg["account_map"].items()}
    keywords = {fid: [norm(k) for k in kws] for fid, kws in cfg["fund_keywords"].items()}
    out, unmapped, skipped_account = [], [], 0
    for r in rows:
        date = norm(r[0]).replace("/", "-")
        name = norm(r[1])
        account = account_map.get(norm(r[6]))
        units, price, amount = parse_int(r[8]), parse_int(r[9]), parse_int(r[13])
        fund_id = next((fid for fid, kws in keywords.items() if any(k in name for k in kws)), None)
        if fund_id is None:
            unmapped.append(name)
            continue
        if account is None or (account == "attack" and not include_attack):
            skipped_account += 1
            continue
        if not (price and amount):
            continue
        rec = {
            "date": date,
            "price": price,
            "category": "別枠積立" if account == "side" else "未分類",
            "amount": amount,
            "account": account,
        }
        if units:
            rec["units"] = units
        out.append((fund_id, rec))
    return out, sorted(set(unmapped)), skipped_account


def is_duplicate(existing: list[dict], rec: dict) -> bool:
    key = (rec["date"], rec["amount"], rec.get("account") or "attack")
    return any((e.get("date"), e.get("amount"), e.get("account") or "attack") == key for e in existing)


def main() -> int:
    parser = argparse.ArgumentParser(description="SBI証券の約定履歴CSVを purchase_history.json に取り込む")
    parser.add_argument("csv", type=Path, help="SBI証券の約定履歴CSV")
    parser.add_argument("--include-attack", action="store_true", help="攻撃フェーズ（成長投資枠）の約定も取り込む（区分は「未分類」）")
    parser.add_argument("--dry-run", action="store_true", help="書き込まず取り込み予定だけを表示する")
    args = parser.parse_args()

    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    cfg = settings.get("sbi_import")
    if not cfg:
        raise SystemExit("config/settings.json に sbi_import の設定がありません")

    history = json.loads(PURCHASE_FILE.read_text(encoding="utf-8")) if PURCHASE_FILE.exists() else {}
    records, unmapped, skipped_account = build_records(read_csv_rows(args.csv), cfg, args.include_attack)

    added, dup = [], 0
    for fund_id, rec in records:
        bucket = history.setdefault(fund_id, [])
        if is_duplicate(bucket, rec):
            dup += 1
            continue
        bucket.append(rec)
        added.append((fund_id, rec))

    for fund_id, rec in added:
        print(f"  + {fund_id:14s} {rec['date']}  {rec['price']:>7,}円  {rec['amount']:>8,}円  {rec['account']}  {rec['category']}")
    print(f"取り込み対象: {len(added)}件 / 重複スキップ: {dup}件 / 口座対象外（成長投資枠など）: {skipped_account}件")
    if unmapped:
        print("監視対象外として無視した銘柄: " + " / ".join(unmapped))

    if added and not args.dry_run:
        for records_ in history.values():
            records_.sort(key=lambda r: r.get("date", ""))
        PURCHASE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{PURCHASE_FILE} に書き込みました")
    elif args.dry_run:
        print("[dry-run] 書き込みは行っていません")
    return 0


if __name__ == "__main__":
    sys.exit(main())
