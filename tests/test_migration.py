import unittest
from datetime import date

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import migrate_history_asof as mig

COLS = ["a", "b"]


def row(d, a="", b=""):
    return {"date": d, "a": a, "b": b}


class FundRelabelTest(unittest.TestCase):
    def test_business_day_skips_weekends_and_holidays(self):
        self.assertEqual(mig.prev_jp_business_day(date(2026, 11, 24)), date(2026, 11, 20))   # 11/23は祝日
        self.assertEqual(mig.prev_jp_business_day(date(2026, 11, 23)), date(2026, 11, 20))   # 月曜 → 金曜
        self.assertEqual(mig.prev_jp_business_day(date(2026, 11, 25)), date(2026, 11, 24))

    def test_relabels_merges_same_values_and_drops_empty_rows(self):
        rows = [row("2026-07-07", "10", "20"), row("2026-07-08"),
                row("2026-11-23", "100", "200"), row("2026-11-24", "100", "200"), row("2026-11-25", "110", "210")]
        new, log, conflicts = mig.relabel_fund_rows(rows, COLS)
        self.assertEqual([(r["date"], r["a"]) for r in new], [("2026-07-07", "10"), ("2026-11-20", "100"), ("2026-11-24", "110")])
        self.assertEqual(conflicts, [])
        self.assertEqual([x["action"] for x in log], ["keep", "drop_empty", "relabel", "merge", "relabel"])

    def test_different_values_for_the_same_as_of_are_reported_and_block_apply(self):
        new, _, conflicts = mig.relabel_fund_rows([row("2026-11-23", "100"), row("2026-11-24", "101")], COLS)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(new[0]["a"], "100")   # 先の値を残す

    def test_purchase_price_matches_only_after_relabeling(self):
        rows = [row("2026-09-25", "16833"), row("2026-09-28", "16941")]       # 実行日のキー（旧）
        purchases = {"a": [{"date": "2026-09-25", "price": 16941, "currency": "JPY"}]}
        self.assertEqual(mig.match_purchases(rows, purchases, ["a"])[:2], (0, 1))
        new, _, _ = mig.relabel_fund_rows(rows, COLS)
        self.assertEqual(mig.match_purchases(new, purchases, ["a"])[:2], (1, 1))


class StockAndPeakTest(unittest.TestCase):
    TRADING = ["2026-11-20", "2026-11-23", "2026-11-24"]

    def test_stock_rows_use_the_latest_trading_day_before_the_run_date(self):
        rows = [{"date": "2026-11-24", "tesla": "385.46", "usdjpy": "159.14"}, {"date": "2026-11-25", "tesla": "384.68", "usdjpy": "159.28"}]
        new, log, conflicts = mig.relabel_stock_rows(rows, ["tesla", "usdjpy"], self.TRADING)
        self.assertEqual([(r["date"], r["tesla"]) for r in new], [("2026-11-23", "385.46"), ("2026-11-24", "384.68")])
        self.assertEqual(conflicts, [])

    def test_holiday_run_merges_into_the_same_trading_day(self):
        rows = [{"date": "2026-11-24", "tesla": "385.46", "usdjpy": "159.14"}, {"date": "2026-11-25", "tesla": "385.46", "usdjpy": "160.00"}]
        trading = ["2026-11-23"]    # 11/24 が休場の想定
        new, log, conflicts = mig.relabel_stock_rows(rows, ["tesla", "usdjpy"], trading)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["usdjpy"], "159.14")   # usdjpy は先の値を残し、不一致にしない
        self.assertEqual(conflicts, [])

    def test_peak_dates_move_only_when_the_old_row_has_the_same_value(self):
        fund_rows = [row("2026-10-08", "17337")]
        _, fund_log, _ = mig.relabel_fund_rows(fund_rows, COLS)
        peak = {"a": {"value": 17337.0, "date": "2026-10-08"},      # 履歴由来 → 付け替え
                "b": {"value": 175.0, "date": "2026-10-08"}}        # 同じ日付でも値が違う（約定単価など）→ 変更なし
        new, changes = mig.relabel_peak(peak, fund_log, fund_rows, [], [])
        self.assertEqual(new["a"]["date"], "2026-10-07")
        self.assertEqual(new["b"]["date"], "2026-10-08")
        self.assertEqual([c["asset"] for c in changes], ["a"])


if __name__ == "__main__":
    unittest.main()
