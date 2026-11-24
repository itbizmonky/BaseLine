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
RESULTS = [
    {"short_name": "A", "drawdown": 5.0, "avg_cost_ratio": 1.5},      # 最高比マイナス・平均比プラス
    {"short_name": "B", "drawdown": 0.0, "avg_cost_ratio": None},     # 高値圏・購入実績なし
    {"short_name": "C", "drawdown": None, "avg_cost_ratio": None},    # 取得失敗
]


def flex_texts(node, out=None):
    """Flexメッセージ内の text 要素を（文言, 色）で集める。"""
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append((node["text"], node.get("color")))
        for v in node.values():
            flex_texts(v, out)
    elif isinstance(node, list):
        for v in node:
            flex_texts(v, out)
    return out


class NotifyMessageTest(unittest.TestCase):
    def test_tier_message_uses_short_labels_and_two_values_only(self):
        msg = notify.build_tier_message("テスト銘柄", "テスト", 2, 13.456, -4.2, "https://example.com/")
        self.assertIn("最高比: -13.46%", msg)
        self.assertIn("平均比: -4.2%", msg)
        self.assertIn("🔴 最高比", msg)  # マイナスは🔴
        for word in FORBIDDEN + ["最高値比", "平均取得単価比"]:
            self.assertNotIn(word, msg)

    def test_daily_summary_text_marks_plus_minus_and_uses_short_labels(self):
        msg = notify.build_daily_summary_message("2026-11-24", RESULTS, "https://example.com/")
        self.assertIn("🔴-5.0%", msg)   # 最高比（マイナス）
        self.assertIn("🟢+1.5%", msg)   # 平均比（プラス）
        self.assertIn("⚪取得不可", msg)
        for word in FORBIDDEN + ["最高値比", "平均取得単価比"]:
            self.assertNotIn(word, msg)

    def test_flex_colors_follow_sign(self):
        bubble = notify.build_daily_summary_flex("2026-11-24", RESULTS, "https://example.com/")
        texts = dict(flex_texts(bubble))
        self.assertEqual(texts["-5.0%"], notify.COLOR_MINUS)    # マイナスは赤
        self.assertEqual(texts["+1.5%"], notify.COLOR_PLUS)     # プラスは緑
        self.assertEqual(texts["0.0%"], notify.COLOR_NEUTRAL)   # ゼロはグレー
        self.assertEqual(texts["-"], notify.COLOR_NEUTRAL)      # データなしはグレー
        labels = [x for x, _ in flex_texts(bubble)]
        self.assertIn("最高比", labels)
        self.assertIn("平均比", labels)
        self.assertEqual(bubble["type"], "bubble")

    def test_tier_flex_shows_only_two_ratios(self):
        bubble = notify.build_tier_flex("テスト銘柄", "テスト", 1, 12.2, -6.8, "https://example.com/")
        texts = [x for x, _ in flex_texts(bubble)]
        self.assertIn("-12.20%", texts)
        self.assertIn("-6.8%", texts)
        for word in FORBIDDEN:
            self.assertNotIn(word, "".join(texts))

    def test_flex_failure_falls_back_to_text(self):
        sent = []
        def fake_push(messages):
            sent.append(messages[0]["type"])
            return messages[0]["type"] == "text"   # Flexは失敗、テキストは成功
        with mock.patch.object(notify, "_push", side_effect=fake_push):
            self.assertTrue(notify.notify_daily_summary("2026-11-24", RESULTS, "https://example.com/"))
        self.assertEqual(sent, ["flex", "text"])

    def test_notify_functions_send_through_line_api_mock(self):
        asset = {"name": "テスト銘柄", "short_name": "テスト"}
        with mock.patch.object(notify, "_push", return_value=True) as push:
            self.assertTrue(notify.notify_tier_reached(asset, 1, 10.0, None, "https://example.com/"))
            self.assertTrue(notify.notify_daily_summary("2026-11-24", RESULTS, "https://example.com/"))
            self.assertTrue(notify.notify_fetch_error(["A"], "2026-11-24", "https://example.com/"))
        self.assertEqual(push.call_count, 3)
        kinds = [c.args[0][0]["type"] for c in push.call_args_list]
        self.assertEqual(kinds, ["flex", "flex", "text"])


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
