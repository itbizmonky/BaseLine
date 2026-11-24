import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

from funding import calc_capital_status, calc_nisa_usage, monthly_cash_amount
from portfolio import build_integrated_view

SETTINGS = helpers.load_settings()


class PortfolioTest(unittest.TestCase):
    HISTORY = {
        "sp500": [{"units": 10000, "amount": 1}],            # core
        "sox": [{"units": 10000, "amount": 1}],              # satellite
        "nvda": [{"units": 2, "amount": 1, "currency": "USD", "price": 100.0}],  # satellite（USD）
        "newnisa_orkan": [{"units": 10000, "amount": 1, "account": "child"}],    # 別会計
        "sbi_v_sp500": [
            {"units": 10000, "amount": 1, "account": "side"},
            {"units": 10000, "amount": 1, "account": "legacy"},                  # 旧NISAも保有に含める
        ],
    }
    PRICES = {"sp500": 40000, "sox": 40000, "nvda": 150.0, "newnisa_orkan": 20000, "sbi_v_sp500": 40000}

    def test_shares_roles_and_usd_conversion(self):
        view = build_integrated_view(SETTINGS, self.HISTORY, self.PRICES, usdjpy=150.0)
        # sp500 40,000 + sbi_v(side 40,000 + legacy 40,000) + sox 40,000 + nvda 2×150×150=45,000
        self.assertAlmostEqual(view["total_value"], 40000 + 80000 + 40000 + 45000)
        self.assertAlmostEqual(view["core_value"], 120000)
        self.assertAlmostEqual(view["satellite_value"], 85000)
        self.assertAlmostEqual(view["core_share"] + view["satellite_share"], 100.0)
        self.assertEqual([s["id"] for s in view["separate"]], ["newnisa_orkan"])  # 分母に含めない
        self.assertEqual(view["rows"][0]["id"], "sbi_v_sp500")                    # 評価額の大きい順

    def test_missing_price_or_fx_makes_total_unavailable(self):
        view = build_integrated_view(SETTINGS, self.HISTORY, self.PRICES, usdjpy=None)
        self.assertIsNone(view["total_value"])
        self.assertIn("エヌビディア", view["missing"])
        view = build_integrated_view(SETTINGS, self.HISTORY, {**self.PRICES, "sox": None}, usdjpy=150.0)
        self.assertIsNone(view["satellite_share"])


class FundingTest(unittest.TestCase):
    def test_capital_counts_only_attack_account_since_start(self):
        history = {
            "a": [
                {"date": "2026-07-07", "amount": 700000, "account": "attack"},
                {"date": "2026-07-10", "amount": 10000, "account": "side"},      # カード積立は原資を消費しない
                {"date": "2026-08-06", "amount": 75000, "account": "child"},     # 子供用も消費しない
                {"date": "2026-06-30", "amount": 999999, "account": "attack"},   # 開始日より前は対象外
                {"date": "2026-11-12", "amount": 100000},                        # account省略=attack
            ],
        }
        cap = calc_capital_status(history, {"initial_jpy": 1000000, "start_date": "2026-07-07"}, monthly_cash=50000)
        self.assertEqual(cap["used_jpy"], 800000)
        self.assertAlmostEqual(cap["months_remaining"], 4.0)
        self.assertAlmostEqual(cap["used_percent"], 80.0)

    def test_nisa_usage_by_year_and_frame(self):
        history = {
            "a": [
                {"date": "2026-03-10", "amount": 10000, "frame": "tsumitate"},
                {"date": "2026-11-12", "amount": 240000, "frame": "growth"},
                {"date": "2025-12-31", "amount": 500000, "frame": "growth"},     # 前年は対象外
                {"date": "2026-11-13", "amount": 1},                              # frame不明は無視
            ],
        }
        usage = calc_nisa_usage(history, 2026, {"growth": 2400000, "tsumitate": 1200000})
        self.assertAlmostEqual(usage["growth"]["percent"], 10.0)
        self.assertEqual(usage["tsumitate"]["used_jpy"], 10000)

    def test_monthly_cash_amount_follows_phases(self):
        self.assertEqual(monthly_cash_amount(SETTINGS, "2026-10-01"), 58824)  # 旧4本
        self.assertEqual(monthly_cash_amount(SETTINGS, "2026-12-01"), 58824)  # NASDAQ100＋Tracers
        self.assertEqual(monthly_cash_amount(SETTINGS, "2026-07-01"), 0)       # 設定開始前


if __name__ == "__main__":
    unittest.main()
