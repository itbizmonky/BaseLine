"""
update_purchases.py
===================
SBI証券の約定履歴CSVの取り込みから、コミット・プッシュまでを1コマンドで行うスクリプト（手動実行）。

    python scripts/update_purchases.py <約定履歴CSV> [--yes] [--no-push]

流れ:
  1. CSVから取り込み予定（攻撃フェーズ・別枠積立とも）を表示する
  2. 確認（y/N）のうえ data/purchase_history.json に書き込む
  3. 銘柄ごとの平均取得単価（攻撃フェーズ分）を表示する
  4. data/purchase_history.json だけをコミットし、リモートの日次更新を取り込んで（pull --rebase）プッシュする
     （プッシュ前にもう一度確認する。--no-push ならコミットまで）

CSVは個人の取引明細のため、公開リポジトリにコミットしない（このスクリプトもCSVは追加しない）。
--yes を付けると確認を省略する（対話できない環境で使う場合）。
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from import_sbi_history import PURCHASE_FILE, ROOT, import_csv
from purchase_history import ACCOUNT_ATTACK, calc_average_cost, filter_by_account, load_purchase_history


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
    parser.add_argument("csv", type=Path, help="SBI証券の約定履歴CSV")
    parser.add_argument("--yes", action="store_true", help="確認を省略する")
    parser.add_argument("--no-push", action="store_true", help="コミットまでで、プッシュしない")
    args = parser.parse_args()

    print("=== 取り込み予定 ===")
    preview = import_csv(args.csv, include_attack=True, write=False)
    if not preview["added"]:
        print("新しく取り込む約定はありません。終了します。")
        return 0
    if not confirm(f"{len(preview['added'])}件を data/purchase_history.json に書き込みますか？", args.yes):
        print("中止しました（何も変更していません）。")
        return 1

    import_csv(args.csv, include_attack=True, write=True)

    print("\n=== 平均取得単価（攻撃フェーズ分） ===")
    history = load_purchase_history()
    for fund_id in sorted({f for f, _ in preview["added"]}):
        avg = calc_average_cost(filter_by_account(history.get(fund_id, []), ACCOUNT_ATTACK))
        if avg:
            print(f"  {fund_id:14s} {avg['avg_cost']:>9,.0f}円（{avg['count']}件・投入{avg['total_amount']:,.0f}円）")

    rel = PURCHASE_FILE.relative_to(ROOT).as_posix()
    if git("diff", "--quiet", "--", rel).returncode == 0:
        print("purchase_history.json に変更がないため、コミットは不要です。")
        return 0
    if not confirm("purchase_history.json をコミットしますか？", args.yes):
        print("書き込みは済んでいますが、コミットはしていません（git で手動対応してください）。")
        return 1

    print(git("add", rel).stderr, end="")
    res = git("commit", "-m", f"📥 購入実績を更新（SBI約定履歴CSVから{len(preview['added'])}件取り込み）", "--", rel)
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
