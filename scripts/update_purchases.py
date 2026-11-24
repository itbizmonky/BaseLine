"""
update_purchases.py
===================
SBI証券の約定履歴CSV（投資信託・米国株）の取り込みから、コミット・プッシュまでを1コマンドで行うスクリプト（手動実行）。

    python scripts/update_purchases.py <約定履歴CSV> [<約定履歴CSV> ...] [--yes] [--no-push]

流れ:
  1. CSVから取り込み予定（口座区分・NISA枠つき）を表示する
  2. 確認（y/N）のうえ data/purchase_history.json に書き込む
  3. 銘柄ごとの平均取得単価（旧NISAを除く）を表示し、資金管理（原資で積立が続けられる月数・NISA枠の使用率）を
     data/funding_status.json に更新する（原資の総額は Personal/capital.json。Git管理外・公開しない）
  4. data/purchase_history.json と data/funding_status.json だけをコミットし、リモートの日次更新を取り込んで
     （pull --rebase）プッシュする（プッシュ前にもう一度確認する。--no-push ならコミットまで）

CSVは個人の取引明細のため、公開リポジトリにコミットしない（このスクリプトもCSVは追加しない）。
--yes を付けると確認を省略する（対話できない環境で使う場合）。
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from funding import STATUS_PATH, update_status
from import_sbi_history import PURCHASE_FILE, ROOT, import_csv
from purchase_history import CURRENCY_USD, calc_average_cost, cost_records, load_purchase_history


def confirm(message: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{message} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        # 対話できない環境（入力が空）では、安全側に倒して中止する
        print("\n対話できない環境です。--yes を付けるか、対話できる端末で実行してください。")
        return False


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="約定履歴CSVの取り込みからコミット・プッシュまでを行う")
    parser.add_argument("csv", type=Path, nargs="+", help="SBI証券の約定履歴CSV（投信・米国株。複数可）")
    parser.add_argument("--yes", action="store_true", help="確認を省略する")
    parser.add_argument("--no-push", action="store_true", help="コミットまでで、プッシュしない")
    args = parser.parse_args()

    print("=== 取り込み予定 ===")
    preview = import_csv(args.csv, write=False)
    changes = len(preview["added"]) + preview["enriched"]
    if not changes:
        print("新しく取り込む約定はありません。終了します。")
        return 0
    if not confirm(f"追加{len(preview['added'])}件・補完{preview['enriched']}件を data/purchase_history.json に書き込みますか？", args.yes):
        print("中止しました（何も変更していません）。")
        return 1

    import_csv(args.csv, write=True)

    print("\n=== 平均取得単価（旧NISAを除く全口座） ===")
    history = load_purchase_history()
    for asset_id in sorted({a for a, _ in preview["added"]}):
        avg = calc_average_cost(cost_records(history.get(asset_id, [])))
        if avg:
            unit = "USD" if avg["currency"] == CURRENCY_USD else "円"
            print(f"  {asset_id:14s} {avg['avg_cost']:>11,.2f}{unit}（{avg['count']}件・投入{avg['total_amount']:,.0f}円）")

    print("\n=== 資金管理 ===")
    status = update_status()
    cap = status.get("capital")
    if cap:
        print(f"  原資の使用率 {cap['used_percent']}% / 積立が続けられる月数 {cap['months_remaining']}か月")
    print(f"  NISA枠の使用率（{status['nisa']['year']}年） 成長 {status['nisa']['growth_percent']}% / つみたて {status['nisa']['tsumitate_percent']}%")

    rels = [PURCHASE_FILE.relative_to(ROOT).as_posix(), STATUS_PATH.relative_to(ROOT).as_posix()]
    if all(git("diff", "--quiet", "--", rel).returncode == 0 for rel in rels):
        print("コミット対象に変更がないため、コミットは不要です。")
        return 0
    if not confirm("purchase_history.json と funding_status.json をコミットしますか？", args.yes):
        print("書き込みは済んでいますが、コミットはしていません（git で手動対応してください）。")
        return 1

    print(git("add", *rels).stderr, end="")
    res = git("commit", "-m", f"📥 購入実績を更新（SBI約定履歴CSVから追加{len(preview['added'])}件・補完{preview['enriched']}件）", "--", *rels)
    if res.returncode != 0:
        print("コミットに失敗しました:\n" + res.stdout + res.stderr)
        return 1
    print("コミットしました。")

    if args.no_push:
        print("--no-push のためプッシュしていません。")
        return 0
    if not confirm("リモート（origin/main）にプッシュしますか？", args.yes):
        print("プッシュはしていません（後で git push してください）。")
        return 0
    pull = git("pull", "--rebase", "--autostash", "origin", "main")
    if pull.returncode != 0:
        print("リモートの取り込み（pull --rebase）に失敗しました。手動で解決してください:\n" + pull.stdout + pull.stderr)
        return 1
    push = git("push", "origin", "main")
    print(push.stdout + push.stderr, end="")
    if push.returncode != 0:
        print("プッシュに失敗しました。")
        return 1
    print("プッシュしました。次回の自動実行で、ダッシュボードに反映されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
