import copy
import unittest

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import notify
from assets import all_assets
from groups import build_groups, schedule_amounts
from portfolio import build_integrated_view

SETTINGS = helpers.load_settings()
IDS = [a["id"] for a in all_assets(SETTINGS)]


def two_source_settings() -> dict:
    """同じ銘柄（NASDAQ100）が原資枠とクレカ枠の両方にある積立設定（2026-11-26以前の構成）。枠分けの汎用ロジックの確認用。"""
    s = copy.deepcopy(SETTINGS)
    cash = s["monthly_schedule"]["cash"]
    cash["phases"] = [p for p in cash["phases"] if p["from"] != "2027-01-23"]
    card = s["monthly_schedule"]["card"]
    last = max(card["phases"], key=lambda p: p["from"])
    last["items"] = [
        {"fund": "eMAXIS Slim 全世界株式（オルカン）", "fund_id": "orkan", "amount": 30000, "frame": "tsumitate"},
        {"fund": "eMAXIS Slim 米国株式（S&P500）", "fund_id": "sp500", "amount": 10000, "frame": "tsumitate"},
        {"fund": "NASDAQ100", "fund_id": "nasdaq100", "amount": 10000, "frame": "growth"},
    ]
    return s


TWO_SOURCE = two_source_settings()

# 口数（投資信託の評価額＝口数×価格/10000。価格10,000なら評価額＝口数）
PURCHASES = {
    "nasdaq100": [{"units": 100, "amount": 1000, "account": "attack"}, {"units": 300, "amount": 3000, "account": "side"}],
    "tracers": [{"units": 50, "amount": 500, "account": "attack"}],
    "orkan": [{"units": 10, "amount": 100, "account": "attack"}, {"units": 5, "amount": 50, "account": "side"}],
    "sp500": [{"units": 20, "amount": 200, "account": "attack"}],
    "sbi_v_sp500": [{"units": 200, "amount": 2000, "account": "side"}, {"units": 800, "amount": 400, "account": "legacy"}],
    "sox": [{"units": 30, "amount": 300, "account": "attack"}],
    "newnisa_orkan": [{"units": 100, "amount": 1000, "account": "child"}],
    "nvda": [{"units": 2, "amount": 30000, "currency": "USD", "price": 100.0, "account": "attack"}],
}


def make_results(price=10000.0):
    out = []
    for a in all_assets(SETTINGS):
        out.append({
            "asset_id": a["id"], "name": a["name"], "short_name": a["short_name"], "kind": a["kind"],
            "currency": a["currency"], "role": a["role"], "color": a["color"], "alert": a["alert"],
            "thresholds": a["tiers"], "price": 100.0 if a["kind"] == "stock" else price,
            "drawdown": 5.0, "tier": 0, "day_change": 0.5, "avg_cost_ratio": None, "stale": False,
        })
    return out


class GroupsTest(unittest.TestCase):
    """枠分けの汎用ロジック（同じ銘柄が2つの資金源にある構成で確認）。"""

    def setUp(self):
        self.groups = {g["key"]: g for g in build_groups(TWO_SOURCE, PURCHASES, make_results(), 150.0, "2026-11-24")}

    def ids(self, key):
        return [ln["asset_id"] for ln in self.groups[key]["lines"]]

    def test_five_groups_in_the_requested_order(self):
        self.assertEqual(list(self.groups), ["cash", "card", "hold", "legacy", "child"])

    def test_membership_follows_latest_monthly_schedule(self):
        self.assertEqual(schedule_amounts(TWO_SOURCE, "cash"), {"nasdaq100": 30000, "tracers": 28824})
        self.assertEqual(self.ids("cash"), ["tracers", "nasdaq100"])
        self.assertEqual(self.ids("card"), ["sp500", "orkan", "nasdaq100"])
        self.assertEqual(self.ids("hold"), ["sbi_v_sp500", "sox", "nvda"])   # 保有のないテスラは出ない
        self.assertEqual(self.ids("legacy"), ["sbi_v_sp500"])
        self.assertEqual(self.ids("child"), ["newnisa_orkan"])

    def test_monthly_totals_and_future_note(self):
        self.assertEqual(self.groups["cash"]["monthly_total"], 58824)
        self.assertEqual(self.groups["card"]["monthly_total"], 50000)
        self.assertEqual(self.groups["card"]["note"], "2027/1/10発注分からの設定")  # 最新の構成はまだ始まっていない
        self.assertIsNone(self.groups["cash"]["note"])

    def test_fund_in_two_groups_is_split_by_funding_source(self):
        cash = {ln["asset_id"]: ln for ln in self.groups["cash"]["lines"]}["nasdaq100"]
        card = {ln["asset_id"]: ln for ln in self.groups["card"]["lines"]}["nasdaq100"]
        self.assertEqual((cash["tag"], cash["units"], cash["monthly"]), ("原資買付分", 100, 30000))
        self.assertEqual((card["tag"], card["units"], card["monthly"]), ("カード積立分", 300, 10000))
        # 価格に関する指標は同じ
        self.assertEqual((cash["drawdown"], cash["day_change"]), (card["drawdown"], card["day_change"]))

    def test_single_group_fund_uses_all_non_legacy_holdings(self):
        orkan = self.groups["card"]["lines"][1]
        self.assertEqual((orkan["asset_id"], orkan["units"]), ("orkan", 15))     # 原資買付10＋カード積立5

    def test_legacy_is_split_from_the_fund(self):
        hold = {ln["asset_id"]: ln for ln in self.groups["hold"]["lines"]}["sbi_v_sp500"]
        legacy = self.groups["legacy"]["lines"][0]
        self.assertEqual((hold["units"], hold["tag"]), (200, "つみたて分"))
        self.assertEqual(legacy["units"], 800)
        # 旧NISA分は取得単価5,000円（400円÷800口×10,000）に対して、現在値10,000円は+100%
        self.assertAlmostEqual(legacy["avg_cost_ratio"], 100.0)

    def test_values_and_shares_add_up_and_child_is_outside_the_denominator(self):
        total = sum(g["value"] for k, g in self.groups.items() if k != "child")
        # 100+300（nasdaq）+50+10+5+20+200+800+30＝1,515口、NVDAは2株×100USD×150円＝30,000円
        self.assertAlmostEqual(total, 1515 + 30000)
        self.assertAlmostEqual(sum(g["share"] for k, g in self.groups.items() if k != "child"), 100.0)
        self.assertIsNone(self.groups["child"]["share"])
        self.assertAlmostEqual(self.groups["child"]["value"], 100)
        # 全体の評価額は、銘柄ごとの統合ビューと一致する
        view = build_integrated_view(SETTINGS, PURCHASES, {a: (100.0 if a in ("nvda", "tesla") else 10000.0) for a in IDS}, 150.0)
        self.assertAlmostEqual(view["total_value"], total)

    def test_missing_price_makes_group_value_unavailable(self):
        results = make_results()
        results[IDS.index("sox")]["price"] = None
        groups = {g["key"]: g for g in build_groups(SETTINGS, PURCHASES, results, 150.0, "2026-11-24")}
        self.assertIsNone(groups["hold"]["value"])
        self.assertIsNone(groups["hold"]["share"])


class CurrentScheduleTest(unittest.TestCase):
    """現行の積立設定（2026-11-26に決定した配分。クレカ2027/1/10・現金2027/1/23発注分から）で枠分けが期待どおりになること。"""

    def setUp(self):
        self.groups = {g["key"]: g for g in build_groups(SETTINGS, PURCHASES, make_results(), 150.0, "2026-11-26")}

    def test_membership_and_amounts(self):
        self.assertEqual(schedule_amounts(SETTINGS, "cash"), {"nasdaq100": 40000, "tracers": 18824})
        self.assertEqual(schedule_amounts(SETTINGS, "card"), {"orkan": 50000})
        ids = lambda key: [ln["asset_id"] for ln in self.groups[key]["lines"]]
        self.assertEqual(ids("cash"), ["tracers", "nasdaq100"])
        self.assertEqual(ids("card"), ["orkan"])
        self.assertIn("sp500", ids("hold"))                       # S&P500は新規積立を停止＝保持枠（保有は継続）
        self.assertNotIn("sp500", ids("cash") + ids("card"))

    def test_totals_are_unchanged_and_notes_show_start_dates(self):
        self.assertEqual(self.groups["cash"]["monthly_total"], 58824)
        self.assertEqual(self.groups["card"]["monthly_total"], 50000)
        self.assertEqual(self.groups["cash"]["note"], "2027/1/23発注分からの設定")
        self.assertEqual(self.groups["card"]["note"], "2027/1/10発注分からの設定")

    def test_nasdaq100_is_a_single_group_fund_with_all_holdings(self):
        line = {ln["asset_id"]: ln for ln in self.groups["cash"]["lines"]}["nasdaq100"]
        self.assertEqual((line["units"], line["monthly"]), (400, 40000))   # 原資買付100＋カード積立300

    def test_notes_disappear_once_the_schedule_has_started(self):
        groups = {g["key"]: g for g in build_groups(SETTINGS, PURCHASES, make_results(), 150.0, "2027-01-24")}
        self.assertIsNone(groups["cash"]["note"])
        self.assertIsNone(groups["card"]["note"])


class GroupedNotifyTest(unittest.TestCase):
    def setUp(self):
        self.results = make_results()
        self.groups = build_groups(TWO_SOURCE, PURCHASES, self.results, 150.0, "2026-11-24")

    def test_text_has_group_headings_with_monthly_totals(self):
        msg = notify.build_daily_summary_message("2026-11-24", self.results, "https://example.com/", self.groups)
        for heading in ("■ 積立設定（原資枠） 月58,824円", "■ 積立設定（クレカ枠） 月50,000円", "■ 保持枠", "■ 旧NISA", "■ こども枠"):
            self.assertIn(heading, msg)
        self.assertEqual(msg.count("NASDAQ100\n"), 2)          # 原資枠とクレカ枠の両方。補足（原資買付分など）は付けない
        for small in ("原資買付分", "カード積立分", "つみたて分", "発注分からの設定"):
            self.assertNotIn(small, msg)
        self.assertLess(msg.index("■ 積立設定（原資枠）"), msg.index("■ 積立設定（クレカ枠）"))
        self.assertLess(msg.index("■ 保持枠"), msg.index("■ 旧NISA"))

    def test_flex_has_group_headings_and_tags(self):
        bubble = notify.build_daily_summary_flex("2026-11-24", self.results, "https://example.com/", self.groups)
        texts = []

        def walk(n):
            if isinstance(n, dict):
                if n.get("type") == "text":
                    texts.append((n["text"], n.get("color")))
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(bubble)
        labels = [t for t, _ in texts]
        for heading in ("積立設定（原資枠）", "積立設定（クレカ枠）", "保持枠", "旧NISA", "こども枠", "月58,824円", "月50,000円"):
            self.assertIn(heading, labels)
        self.assertEqual(labels.count("NASDAQ100"), 2)          # 原資枠とクレカ枠の両方
        for small in ("原資買付分", "カード積立分", "つみたて分", "2027/1/10発注分からの設定"):   # LINEには小さな補足を出さない
            self.assertNotIn(small, labels)
        self.assertTrue(all(c for _, c in texts))               # すべての文字に色が付いている
        colors = dict(texts)
        for heading in ("積立設定（原資枠）", "積立設定（クレカ枠）", "保持枠", "旧NISA", "こども枠"):
            self.assertEqual(colors[heading], notify.COLOR_GROUP)     # 枠の見出しは全て水色
            self.assertNotEqual(colors[heading], notify.COLOR_TEXT)   # 銘柄名（白）とは違う色


if __name__ == "__main__":
    unittest.main()
