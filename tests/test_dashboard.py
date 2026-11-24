import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

from generate_dashboard import _next_tier_text


class NextTierTextTest(unittest.TestCase):
    TIERS = [11, 17, 33]

    def test_shows_the_price_of_the_next_tier_in_yen(self):
        # 最高値45,727円の11%下 = 40,697円
        self.assertEqual(_next_tier_text(0, self.TIERS, 45727, "JPY"), "次のTier1 (-11%): 40,697円")
        self.assertEqual(_next_tier_text(1, self.TIERS, 45727, "JPY"), "次のTier2 (-17%): 37,953円")

    def test_usd_stock_uses_usd(self):
        self.assertEqual(_next_tier_text(0, [24, 38, 67], 175.0, "USD"), "次のTier1 (-24%): 133.00 USD")

    def test_beyond_tier3_and_missing_peak(self):
        self.assertEqual(_next_tier_text(3, self.TIERS, 100, "JPY"), "Tier3上限超過済")
        self.assertEqual(_next_tier_text(0, self.TIERS, None, "JPY"), "次のTier1 (-11%)")


if __name__ == "__main__":
    unittest.main()
