import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import colorsys
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
        self.assertEqual(_tier_status({**base, "tier": 1}), ("Tier1 到達", "tier-hit tier-1"))
        self.assertEqual(_tier_status({**base, "tier": 2}), ("Tier2 到達", "tier-hit tier-2"))
        self.assertEqual(_tier_status({**base, "tier": 3}), ("Tier3 到達", "tier-hit tier-3"))
        self.assertEqual(_tier_status({**base, "thresholds": []}), ("Tier未設定", "tier-none"))
        self.assertEqual(_tier_status({**base, "drawdown": None}), ("取得失敗", "tier-none"))
        self.assertEqual(_tier_status({**base, "drawdown": None, "stale": True}), ("判定保留", "tier-none"))

    def test_tier_colors_are_purple_levels_distinct_from_plus_minus(self):
        css = (Path(__file__).parent.parent / "scripts" / "generate_dashboard.py").read_text(encoding="utf-8")
        colors = {k: re.search(r"\.val-tier\.tier-%s, \.status-badge\.tier-%s \{\{ color: ([^;]+);" % (k, k), css).group(1)
                  for k in ("1", "2", "3", "ok", "none")}
        for plus_minus in ("var(--green)", "var(--red)"):
            self.assertNotIn(plus_minus, colors.values())      # 未到達を緑にしない（プラス表示と紛らわしい）
        self.assertEqual(len(set(colors.values())), 5)         # Tier1〜3・未到達・未設定は全て別の色
        for level in ("1", "2", "3"):                           # Tier1〜3は紫系（色相250〜310度。オレンジ等のポジティブな色にしない）
            r, g, b = (int(colors[level][i:i + 2], 16) / 255 for i in (1, 3, 5))
            hue = colorsys.rgb_to_hls(r, g, b)[0] * 360
            self.assertTrue(250 <= hue <= 310, f"Tier{level} の色が紫系でない: {colors[level]} (色相{hue:.0f})")
        # 段階が上がるほど色が鮮やかになる（Tier1は淡く、Tier3は最も鮮やか。彩度の指標＝RGBの最大値−最小値）
        chroma = {}
        for lv in ("1", "2", "3"):
            rgb = [int(colors[lv][i:i + 2], 16) for i in (1, 3, 5)]
            chroma[lv] = max(rgb) - min(rgb)
        self.assertLess(chroma["1"], chroma["2"])
        self.assertLess(chroma["2"], chroma["3"])


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
        self.assertIn("取得失敗: " + results[0]["short_name"], html)
        self.assertIn("基準日が古いためTier判定を保留", html)
        self.assertIn("エヌビディア（基準日11/25）", html)


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


class ChartAlignmentTest(unittest.TestCase):
    def test_purchase_marker_sits_on_the_label_of_its_execution_date(self):
        from generate_dashboard import _build_chart_data
        # 履歴の日付は価格の基準日。9/25の約定単価16,941円は、9/25の行の値と同じ位置に載る
        history = [{"date": "2026-09-24", "tracers": 16775.0}, {"date": "2026-09-25", "tracers": 16941.0}, {"date": "2026-09-28", "tracers": 16949.0}]
        purchases = {"tracers": [{"date": "2026-09-25", "price": 16941, "amount": 21765, "category": "追加買付"}]}
        data = _build_chart_data(all_assets(SETTINGS), history, [], SETTINGS, {}, 159.0, purchases)
        marker = data["purchase_markers"]["tracers"][0]
        self.assertEqual(data["labels"][marker["index"]], "2026-09-25")
        series = next(d for d in data["datasets"] if d["id"] == "tracers")["data"]
        self.assertEqual(series[marker["index"]], marker["price"])


class ChartScriptTest(unittest.TestCase):
    def test_chart_update_does_not_use_active_mode(self):
        # update('active') は全ての点をホバー状態（丸）で描画してしまう（タブ切替で丸が密集する不具合の原因）
        from generate_dashboard import _SCRIPT_JS
        self.assertNotIn("update('active')", _SCRIPT_JS)
        self.assertNotIn('update("active")', _SCRIPT_JS)
        self.assertIn("pointRadius: 0", _SCRIPT_JS)   # 通常時は点を描かない


if __name__ == "__main__":
    unittest.main()
