"""
import_sbi_history.py
=====================
SBI証券の約定履歴CSV（投資信託・米国株）を data/purchase_history.json に取り込むスクリプト（手動実行）。

    python scripts/import_sbi_history.py <約定履歴CSV> [<約定履歴CSV> ...] [--dry-run] [--reclassify]

- CSVの形式は先頭の見出し行から自動判定する。
    投資信託CSV: 見出しが「約定日,銘柄,…,預り,課税,約定数量,約定単価,…,受渡金額」（UTF-8 BOM付き）
    米国株CSV  : 見出しが「国内約定日,銘柄,銘柄コード,…,取引,預り区分,約定数量,約定単価(例 151.0000USD),…,受渡金額/決済損益」（CP932）
  列は位置ではなく見出し名で引く。
- 預り区分から NISA の枠（frame）を付ける（settings.json の sbi_import.frame_map）。米国株のNISAは成長投資枠。
- 口座区分 account（資金源）は次の順で決める:
    1. sbi_import.child_funds の銘柄（はじめてのNISA）→ child（子供用・別会計）
    2. つみたて投資枠、または成長投資枠でクレジットカードの積立設定（monthly_schedule.card.phases の
       銘柄・金額・枠が約定日に有効な設定と一致）→ side（カード積立）
    3. それ以外 → attack（原資から出した買付）
  CSVに決済方法の列がないため、同じ月に同じ銘柄・同じ金額でカード積立と手動買付が重なった場合は、日付が
  最も遅い約定をカード積立とみなして警告する（実際は SBI の注文履歴で確認すること）。
- 攻撃フェーズ（attack）の区分は sbi_import.attack_category_rules で日付から推定する（初回購入日→「①分」、
  毎月の積立日（recurring_day_range）→「定期積立」、それ以外→「追加買付」）。
- 重複判定は（銘柄・約定日・金額）の件数照合。同じ日に同じ金額の約定が2件あっても、CSVの件数まで取り込む。
  既存の実績に口数・単価・枠が無ければ補完する（--reclassify を付けると口座区分も CSV から決め直す）。
- CSVは個人の取引明細のため、公開リポジトリにコミットしないこと。
"""

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.json"
PURCHASE_FILE = ROOT / "data" / "purchase_history.json"

KIND_FUND = "fund"          # 投資信託CSV
KIND_US_STOCK = "us_stock"  # 米国株CSV

# 件数照合のとき、カテゴリを CSV から決め直してよい（＝自動付与された汎用の）区分
GENERIC_CATEGORIES = {"", "未分類", "別枠積立", "追加買付", "カード積立"}


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


def parse_number(s: str) -> float | None:
    """'24,098円'・'151.0000USD'・'26293' などから数値を取り出す。解析できなければ None。"""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", norm(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def read_csv_table(path: Path) -> tuple[str, list[dict]]:
    """
    SBI証券のCSV（UTF-8 BOM / CP932）を読み、(形式, 見出し名をキーにした行のリスト) を返す。
    形式は KIND_FUND（見出し先頭が「約定日」）または KIND_US_STOCK（「国内約定日」）。
    """
    if not path.exists():
        raise SystemExit(f"CSVが見つかりません: {path}")
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
    start, kind = None, None
    for i, line in enumerate(lines):
        head = line.lstrip('"').split(",")[0].strip('"')
        if head == "約定日":
            start, kind = i, KIND_FUND
            break
        if head == "国内約定日":
            start, kind = i, KIND_US_STOCK
            break
    if start is None:
        raise SystemExit("「約定日」または「国内約定日」で始まる見出し行が見つかりません（SBI証券の約定履歴CSVではない可能性があります）")
    reader = csv.reader(lines[start:])
    header = [norm(h) for h in next(reader)]
    rows = [dict(zip(header, r)) for r in reader if len(r) >= len(header) - 1 and any(c.strip() for c in r)]
    return kind, rows


def attack_category(date: str, rules: dict | None) -> str:
    """原資から出した約定の区分を日付から推定する（初回購入日→①分、毎月の積立日→定期積立、それ以外→追加買付）。"""
    rules = rules or {}
    if date == rules.get("initial_purchase_date"):
        return rules.get("initial_category", "①分")
    day = int(date[8:10]) if len(date) >= 10 and date[8:10].isdigit() else None
    rng = rules.get("recurring_day_range") or [rules.get("recurring_day_of_month")] * 2
    lo, hi = rng
    if day is not None and lo is not None and lo <= day <= hi:
        return rules.get("recurring_category", "定期積立")
    return rules.get("default", "追加買付")


def phase_at(phases: list[dict], date: str) -> dict | None:
    """date（YYYY-MM-DD）時点で有効な積立設定（from が date 以前で最も新しいもの）を返す。"""
    active = [p for p in phases if p.get("from", "") <= date]
    return max(active, key=lambda p: p["from"]) if active else None


def card_item_matches(card_schedule: dict | None, asset_id: str, amount: int, frame: str | None, date: str) -> bool:
    """約定日に有効なクレジットカード積立設定に、（銘柄・金額・枠）が一致する項目があるか。"""
    phase = phase_at((card_schedule or {}).get("phases", []), date)
    if not phase:
        return False
    return any(
        it.get("fund_id") == asset_id and it.get("amount") == amount and (it.get("frame") in (None, frame))
        for it in phase.get("items", [])
    )


def build_records(rows: list[dict], kind: str, cfg: dict, card_schedule: dict | None) -> tuple[list[tuple[str, dict]], list[str], list[str]]:
    """
    CSV行から (資産ID, レコード) のリストを作る。
    戻り値: (実績, 対応しなかった銘柄名, 警告)
    """
    frame_map = {norm(k): v for k, v in cfg["frame_map"].items()}
    keywords = {aid: [norm(k) for k in kws] for aid, kws in cfg["fund_keywords"].items()}
    tickers = cfg.get("ticker_map") or {}
    child_funds = set(cfg.get("child_funds") or [])
    rules = cfg.get("attack_category_rules")
    out, unmapped, warnings = [], [], []

    for r in rows:
        if kind == KIND_FUND:
            date = norm(r.get("約定日", "")).replace("/", "-")
            name = norm(r.get("銘柄", ""))
            asset_id = next((aid for aid, kws in keywords.items() if any(k in name for k in kws)), None)
            frame = frame_map.get(norm(r.get("預り", "")))
            units, price, amount = parse_number(r.get("約定数量")), parse_number(r.get("約定単価")), parse_number(r.get("受渡金額"))
            currency = "JPY"
            if r.get("取引") and "買" not in r["取引"]:
                warnings.append(f"買付以外の取引をスキップ: {date} {name} {r['取引']}")
                continue
        else:
            date = norm(r.get("国内約定日", "")).replace("/", "-")
            name = norm(r.get("銘柄", ""))
            asset_id = tickers.get(norm(r.get("銘柄コード", "")))
            frame = "growth"  # 米国株のNISAは成長投資枠のみ
            units, price, amount = parse_number(r.get("約定数量")), parse_number(r.get("約定単価")), parse_number(r.get("受渡金額/決済損益"))
            currency = "USD"
            if "買" not in norm(r.get("取引", "")):
                warnings.append(f"買付以外の取引をスキップ: {date} {name} {r.get('取引')}")
                continue
            if "NISA" not in norm(r.get("預り区分", "")):
                warnings.append(f"NISA以外の預り区分（{r.get('預り区分')}）: {date} {name}（取り込みます）")
                frame = None
        if asset_id is None:
            unmapped.append(name)
            continue
        if not (price and amount):
            warnings.append(f"単価または金額が読めないためスキップ: {date} {name}")
            continue
        rec = {"date": date, "price": int(price) if currency == "JPY" else round(price, 4), "amount": int(amount)}
        if units:
            rec["units"] = int(units)
        if currency == "USD":
            rec["currency"] = "USD"
        if frame:
            rec["frame"] = frame
        out.append((asset_id, rec))

    # 口座区分（資金源）を決める
    card_candidates: dict[tuple, list[int]] = {}
    for idx, (asset_id, rec) in enumerate(out):
        if asset_id in child_funds:
            rec["account"] = "child"
            rec["category"] = "子供用積立"
        elif rec.get("frame") == "tsumitate":
            rec["account"] = "side"
            rec["category"] = "カード積立"
        elif rec.get("currency") != "USD" and card_item_matches(card_schedule, asset_id, rec["amount"], rec.get("frame"), rec["date"]):
            card_candidates.setdefault((asset_id, rec["date"][:7], rec["amount"]), []).append(idx)
            rec["account"] = "side"
            rec["category"] = "カード積立"
        else:
            rec["account"] = "attack"
            rec["category"] = attack_category(rec["date"], rules)
    # 同じ月・銘柄・金額でカード積立の候補が複数 → 日付が最も遅い1件だけをカード積立にする
    for (asset_id, month, amount), idxs in card_candidates.items():
        if len(idxs) <= 1:
            continue
        idxs.sort(key=lambda i: out[i][1]["date"])
        for i in idxs[:-1]:
            out[i][1]["account"] = "attack"
            out[i][1]["category"] = attack_category(out[i][1]["date"], rules)
        dates = ", ".join(out[i][1]["date"] for i in idxs)
        warnings.append(
            f"要確認: {asset_id} {amount:,}円 が {month} に{len(idxs)}件（{dates}）。日付が最も遅い約定をカード積立、"
            "それ以外を手動買付（原資）として取り込みます。SBIの注文履歴（取引種別）で確認してください"
        )
    return out, sorted(set(unmapped)), warnings


def merge_into(history: dict, records: list[tuple[str, dict]], reclassify: bool = False) -> dict:
    """
    records を history に（銘柄・約定日・金額）の件数照合でマージする。
    - 既存に同じキーの未照合の実績があれば重複とし、無い項目（口数・単価・枠・通貨）だけを補完する
      （reclassify=True なら口座区分と汎用の区分も CSV から決め直す）
    - 無ければ追加する
    Returns: {"added": [(資産ID, rec)], "duplicates": int, "enriched": int}
    """
    added, dup, enriched = [], 0, 0
    claimed: dict[str, set[int]] = {}
    for asset_id, rec in records:
        bucket = history.setdefault(asset_id, [])
        used = claimed.setdefault(asset_id, set())
        key = (rec["date"], rec["amount"])
        idx = next((i for i, e in enumerate(bucket) if i not in used and (e.get("date"), e.get("amount")) == key), None)
        if idx is None:
            bucket.append(rec)
            used.add(len(bucket) - 1)
            added.append((asset_id, rec))
            continue
        used.add(idx)
        dup += 1
        e = bucket[idx]
        changed = False
        for field in ("units", "price", "frame", "currency"):
            if field in rec and not e.get(field):
                e[field] = rec[field]
                changed = True
        if reclassify:
            if e.get("account") != rec["account"]:
                e["account"] = rec["account"]
                changed = True
            if e.get("category", "") in GENERIC_CATEGORIES and e.get("category") != rec["category"]:
                e["category"] = rec["category"]
                changed = True
        enriched += int(changed)
    return {"added": added, "duplicates": dup, "enriched": enriched}


def format_record(asset_id: str, rec: dict) -> str:
    price = f"{rec['price']:>9,.2f}USD" if rec.get("currency") == "USD" else f"{rec['price']:>9,}円  "
    return f"  + {asset_id:14s} {rec['date']}  {price}  {rec['amount']:>8,}円  {rec['account']:6s} {rec.get('frame', '-'):9s} {rec['category']}"


def import_csv(csv_paths: list[Path], write: bool, reclassify: bool = False) -> dict:
    """
    CSVを読み、取り込み予定を表示する。write=True の場合は purchase_history.json に書き込む。
    Returns: {"added": [...], "duplicates": int, "enriched": int, "unmapped": [str], "warnings": [str]}
    """
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    cfg = settings.get("sbi_import")
    if not cfg:
        raise SystemExit("config/settings.json に sbi_import の設定がありません")
    card_schedule = (settings.get("monthly_schedule") or {}).get("card")

    history = json.loads(PURCHASE_FILE.read_text(encoding="utf-8")) if PURCHASE_FILE.exists() else {}
    all_records, unmapped, warnings = [], [], []
    for path in csv_paths:
        kind, rows = read_csv_table(path)
        recs, un, warn = build_records(rows, kind, cfg, card_schedule)
        print(f"{path.name}: {'米国株' if kind == KIND_US_STOCK else '投資信託'} {len(rows)}行 → 取込候補{len(recs)}件")
        all_records += recs
        unmapped += un
        warnings += warn
    result = merge_into(history, all_records, reclassify=reclassify)

    for asset_id, rec in result["added"]:
        print(format_record(asset_id, rec))
    print(f"追加: {len(result['added'])}件 / 既存と一致（重複）: {result['duplicates']}件 / うち補完・更新: {result['enriched']}件")
    if unmapped:
        print("監視対象外として無視した銘柄: " + " / ".join(sorted(set(unmapped))))
    for w in warnings:
        print(f"  ⚠ {w}")

    if write and (result["added"] or result["enriched"]):
        for records_ in history.values():
            records_.sort(key=lambda r: r.get("date", ""))
        PURCHASE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{PURCHASE_FILE} に書き込みました")
    return {**result, "unmapped": sorted(set(unmapped)), "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description="SBI証券の約定履歴CSV（投信・米国株）を purchase_history.json に取り込む")
    parser.add_argument("csv", type=Path, nargs="+", help="SBI証券の約定履歴CSV（複数可）")
    parser.add_argument("--dry-run", action="store_true", help="書き込まず取り込み予定だけを表示する")
    parser.add_argument("--reclassify", action="store_true", help="既存の実績の口座区分・汎用の区分もCSVから決め直す")
    args = parser.parse_args()
    import_csv(args.csv, write=not args.dry_run, reclassify=args.reclassify)
    if args.dry_run:
        print("[dry-run] 書き込みは行っていません")
    return 0


if __name__ == "__main__":
    sys.exit(main())
