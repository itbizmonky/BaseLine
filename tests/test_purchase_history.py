import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

from purchase_history import asset_holdings, calc_average_cost, calc_gain_vs_average, cost_records, record_units


class AverageCostTest(unittest.TestCase):
    def test_fund_average_uses_units_when_given(self):
        # 投資信託: Σ投入金額 ÷ Σ口数 × 10,000
        records = [
            {"date": "2026-01-01", "amount": 10000, "units": 2500},
            {"date": "2026-02-01", "amount": 10000, "units": 2000},
        ]
        avg = calc_average_cost(records)
        self.assertAlmostEqual(avg["avg_cost"], 20000 / 4500 * 10000)
        self.assertEqual(avg["currency"], "JPY")

    def test_fund_units_are_derived_from_price_when_missing(self):
        self.assertAlmostEqual(record_units({"amount": 10000, "price": 40000}), 2500)

    def test_usd_average_is_weighted_by_shares(self):
        # 米国株: Σ(株数×USD単価) ÷ Σ株数（円換算の平均は受渡金額 ÷ 株数）
        records = [
            {"amount": 24098, "units": 1, "price": 151.0, "currency": "USD"},
            {"amount": 51565, "units": 2, "price": 161.0, "currency": "USD"},
        ]
        avg = calc_average_cost(records)
        self.assertAlmostEqual(avg["avg_cost"], (151 + 2 * 161) / 3)
        self.assertAlmostEqual(avg["avg_cost_jpy"], (24098 + 51565) / 3)
        self.assertEqual(avg["currency"], "USD")

    def test_usd_without_units_is_excluded_not_guessed(self):
        records = [{"amount": 10000, "price": 100.0, "currency": "USD"}]
        self.assertIsNone(calc_average_cost(records))

    def test_gain_vs_average(self):
        records = [{"amount": 10000, "units": 10000}]  # 取得単価 10,000円
        self.assertAlmostEqual(calc_gain_vs_average(records, 9000), -10.0)
        self.assertIsNone(calc_gain_vs_average([], 9000))
        self.assertIsNone(calc_gain_vs_average(records, None))

    def test_cost_records_exclude_legacy_only(self):
        records = [{"account": "legacy"}, {"account": "side"}, {"account": "child"}, {}]
        self.assertEqual(len(cost_records(records)), 3)


class HoldingsTest(unittest.TestCase):
    def test_holdings_by_account(self):
        records = [
            {"account": "side", "amount": 10000, "units": 100},
            {"account": "legacy", "amount": 5000, "units": 300},
            {"amount": 1000, "units": 50},  # account 省略 = attack
        ]
        h = asset_holdings(records)
        self.assertEqual(h["total_units"], 450)
        self.assertEqual(h["by_account"]["attack"]["units"], 50)
        self.assertEqual(h["by_account"]["legacy"]["units"], 300)


if __name__ == "__main__":
    unittest.main()
