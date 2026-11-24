import unittest
from unittest import mock

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import notify
from assets import all_assets

SETTINGS = helpers.load_settings()
FORBIDDEN = ["投入予定", "BUY", "WAIT", "HOLD", "基準日比", "現在期間", "期間", "原資", "現在値", "設定来高値", "判定"]


class NotifyMessageTest(unittest.TestCase):
    def test_tier_message_has_only_the_two_ratios(self):
        msg = notify.build_tier_message("テスト銘柄", "テスト", 2, 13.456, -4.2, "https://example.com/")
        self.assertIn("最高値比: -13.46%", msg)
        self.assertIn("平均取得単価比: -4.2%", msg)
        for word in FORBIDDEN:
            self.assertNotIn(word, msg)

    def test_daily_summary_lists_every_asset_with_two_ratios(self):
        results = [
            {"short_name": "A", "drawdown": 5.0, "avg_cost_ratio": 1.5},
            {"short_name": "B", "drawdown": 0.0, "avg_cost_ratio": None},
            {"short_name": "C", "drawdown": None, "avg_cost_ratio": None},  # 取得失敗
        ]
        msg = notify.build_daily_summary_message("2026-11-24", results, "https://example.com/")
        self.assertIn("最高値比: -5.0%", msg)
        self.assertIn("平均取得単価比: +1.5%", msg)
        self.assertIn("-（購入実績なし）", msg)
        self.assertIn("取得できませんでした", msg)
        for word in FORBIDDEN:
            self.assertNotIn(word, msg)

    def test_notify_functions_send_through_line_api_mock(self):
        asset = {"name": "テスト銘柄", "short_name": "テスト"}
        with mock.patch.object(notify, "_send_line_message", return_value=True) as send:
            self.assertTrue(notify.notify_tier_reached(asset, 1, 10.0, None, "https://example.com/"))
            self.assertTrue(notify.notify_daily_summary("2026-11-24", [{"short_name": "A", "drawdown": 1.0, "avg_cost_ratio": 0.0}], "https://example.com/"))
            self.assertTrue(notify.notify_fetch_error(["A"], "2026-11-24", "https://example.com/"))
        self.assertEqual(send.call_count, 3)


class SettingsConsistencyTest(unittest.TestCase):
    def test_monthly_schedule_totals_and_matching_keys(self):
        for key, expected in (("cash", 58824), ("card", 50000)):
            phases = SETTINGS["monthly_schedule"][key]["phases"]
            latest = max(phases, key=lambda p: p["from"])
            self.assertEqual(sum(i["amount"] for i in latest["items"]), expected, key)
            for phase in phases:
                for item in phase["items"]:
                    self.assertIn("fund_id", item)
                    self.assertIn(item["frame"], ("growth", "tsumitate"))

    def test_assets_are_consistent(self):
        assets = all_assets(SETTINGS)
        ids = [a["id"] for a in assets]
        self.assertEqual(len(ids), len(set(ids)))
        for a in assets:
            self.assertIn(a["role"], ("core", "satellite", "separate"))
            self.assertLessEqual(len(a["tiers"]), 3)
            self.assertEqual(a["tiers"], sorted(a["tiers"]))
            self.assertEqual(len(a["tiers"]), 3, a["id"])
            self.assertTrue(all(b - a_ >= 5 for a_, b in zip(a["tiers"], a["tiers"][1:])), a["id"])  # 段階の間隔は5pt以上
            if a["kind"] == "fund":
                self.assertTrue(a.get("fund_code"), a["id"])
            else:
                self.assertTrue(a.get("ticker"), a["id"])
        # LINEのTierアラートは積立対象の4銘柄だけ
        self.assertEqual({a["id"] for a in assets if a["alert"]}, {"orkan", "sp500", "tracers", "nasdaq100"})
        # 積立の銘柄はすべて保有資産として定義されている
        for key in ("cash", "card"):
            for phase in SETTINGS["monthly_schedule"][key]["phases"]:
                for item in phase["items"]:
                    self.assertIn(item["fund_id"], ids)


if __name__ == "__main__":
    unittest.main()
