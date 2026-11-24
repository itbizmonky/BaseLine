import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import re
from pathlib import Path

from assets import all_assets
from generate_dashboard import (
    _build_alert_banner, _build_asset_cards, _build_summary_table, _next_tier_text, _render_html, _tier_short, _tier_status,
)
from groups import build_groups


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


SETTINGS = helpers.load_settings()


def sample_results(tiers=None, stale_ids=()):
    """全資産の最小限の監視結果。tiers: {資産ID: 到達Tier}（指定がなければ全て未到達）。"""
    tiers = tiers or {}
    out = []
    for a in all_assets(SETTINGS):
        out.append({
            "asset_id": a["id"], "name": a["name"], "short_name": a["short_name"], "kind": a["kind"],
            "currency": a["currency"], "role": a["role"], "color": a["color"], "alert": a["alert"],
            "thresholds": a["tiers"], "price": 10000.0, "price_date": "2026-11-25", "stale": a["id"] in stale_ids,
            "peak_value": 11000.0, "peak_date": "2026-10-01", "drawdown": 9.0, "tier": tiers.get(a["id"], 0),
            "day_change": 0.5, "avg_cost_ratio": -2.0,
        })
    return out


class AlertBannerTest(unittest.TestCase):
    def test_lists_assets_that_reached_a_tier_with_links_to_their_details(self):
        html = _build_alert_banner(sample_results({"tracers": 1, "sox": 2}))
        self.assertIn("Tier到達中", html)
        self.assertIn('href="#asset-tracers"', html)
        self.assertIn("Tracers（Tier1）", html)
        self.assertIn('href="#asset-sox"', html)
        self.assertNotIn("取得失敗", html)

    def test_no_tier_reached(self):
        self.assertIn("Tier到達なし", _build_alert_banner(sample_results()))

    def test_warns_about_stale_or_missing_prices(self):
        results = sample_results(stale_ids=("nvda",))
        results[0]["price"] = None
        html = _build_alert_banner(results)
        self.assertIn("取得失敗・前回値", html)
        self.assertIn("エヌビディア", html)
        self.assertIn(results[0]["short_name"], html)


class CompactLayoutTest(unittest.TestCase):
    def setUp(self):
        self.results = sample_results({"tracers": 1})
        self.groups = build_groups(SETTINGS, {}, self.results, 150.0, "2026-11-25")

    def test_tier_short_text_for_phones(self):
        self.assertEqual(_tier_short("Tier1 到達"), "Tier1")
        self.assertEqual(_tier_short("Tier未設定"), "未設定")
        self.assertEqual(_tier_short("未到達"), "未到達")

    def test_summary_has_one_row_per_line_with_full_and_short_tier(self):
        html = _build_summary_table(self.groups)
        lines = sum(len(g["lines"]) for g in self.groups)
        self.assertEqual(html.count("<tr><td"), lines)
        self.assertIn('<span class="t-full">Tier1 到達</span><span class="t-short">Tier1</span>', html)

    def test_details_are_collapsible_closed_by_default_and_cover_all_assets(self):
        html = _build_asset_cards(self.results, SETTINGS, {}, None, self.groups)
        for a in all_assets(SETTINGS):
            self.assertIn(f'<details class="asset-acc" id="asset-{a["id"]}"', html)
        self.assertEqual(html.count("<details"), len(all_assets(SETTINGS)))
        self.assertNotIn("<details open", html)
        self.assertIn('class="asset-sum"', html)

    def test_nav_links_point_to_existing_sections(self):
        assets = all_assets(SETTINGS)
        page = _render_html("2026-11-25", _build_alert_banner(self.results), "", "", "", '<section id="sec-funding"></section>',
                            '<section id="sec-portfolio"></section>', "", "{}", SETTINGS, assets)
        hrefs = re.findall(r'<nav class="sec-nav".*?</nav>', page, flags=re.S)[0]
        targets = re.findall(r'href="#(sec-[a-z]+)"', hrefs)
        self.assertEqual(targets, ["sec-summary", "sec-detail", "sec-chart", "sec-market", "sec-funding", "sec-portfolio"])
        for target in targets:
            self.assertIn(f'id="{target}"', page)


if __name__ == "__main__":
    unittest.main()
