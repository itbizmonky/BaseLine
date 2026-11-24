import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import re
from pathlib import Path

from generate_dashboard import _next_tier_text, _tier_status


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


class TierStatusTest(unittest.TestCase):
    def test_states_and_css_classes(self):
        base = {"drawdown": 5.0, "thresholds": [11, 17, 33], "tier": 0}
        self.assertEqual(_tier_status(base), ("未到達", "tier-ok"))
        self.assertEqual(_tier_status({**base, "tier": 2}), ("Tier2 到達", "tier-hit"))
        self.assertEqual(_tier_status({**base, "thresholds": []}), ("Tier未設定", "tier-none"))
        self.assertEqual(_tier_status({**base, "drawdown": None}), ("取得失敗", "tier-none"))

    def test_tier_colors_do_not_clash_with_plus_minus_colors(self):
        css = (Path(__file__).parent.parent / "scripts" / "generate_dashboard.py").read_text(encoding="utf-8")
        colors = {k: re.search(r"\.val-tier\.tier-%s, \.status-badge\.tier-%s \{\{ color: ([^;]+);" % (k, k), css).group(1) for k in ("hit", "ok", "none")}
        for plus_minus in ("var(--green)", "var(--red)"):
            self.assertNotIn(plus_minus, colors.values())      # 未到達を緑にしない（プラス表示と紛らわしい）
        self.assertEqual(len(set(colors.values())), 3)         # 到達・未到達・未設定は別の色


if __name__ == "__main__":
    unittest.main()
