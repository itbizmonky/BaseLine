import json
import tempfile
import unittest
from pathlib import Path

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import judge
from judge import (
    calc_day_change, calc_drawdown, is_new_trigger, judge_tier, load_triggered, notified_tier,
    record_notified, rearm_if_recovered, seed_initial_peak, update_peak,
)

TIERS = [10, 18, 28]


class DrawdownTierTest(unittest.TestCase):
    def test_drawdown_never_negative(self):
        self.assertEqual(calc_drawdown(120, 100), 0.0)
        self.assertAlmostEqual(calc_drawdown(85, 100), 15.0)

    def test_tier_boundaries_and_unset(self):
        self.assertEqual(judge_tier(9.9, TIERS), 0)
        self.assertEqual(judge_tier(10, TIERS), 1)
        self.assertEqual(judge_tier(28, TIERS), 3)
        self.assertEqual(judge_tier(50, []), 0)  # Tier未設定


class DayChangeTest(unittest.TestCase):
    ROWS = [
        {"date": "2026-11-19", "a": 100.0},
        {"date": "2026-11-20", "a": 110.0},
        {"date": "2026-11-23", "a": 110.0},   # 祝日で前日と同値
        {"date": "2026-11-24", "a": 110.0},   # 今日の行（無視される）
    ]

    def test_compares_with_last_different_value_skipping_holiday_duplicates(self):
        self.assertAlmostEqual(calc_day_change(self.ROWS, "a", 121.0, "2026-11-24"), 10.0)

    def test_same_price_looks_back_further_and_today_row_is_ignored(self):
        self.assertAlmostEqual(calc_day_change(self.ROWS, "a", 110.0, "2026-11-24"), 10.0)  # 100→110

    def test_none_when_no_price_or_no_history(self):
        self.assertIsNone(calc_day_change(self.ROWS, "a", None, "2026-11-24"))
        self.assertIsNone(calc_day_change([], "a", 100.0, "2026-11-24"))
        self.assertIsNone(calc_day_change(self.ROWS, "missing", 100.0, "2026-11-24"))


class AlertStateTest(unittest.TestCase):
    def test_notify_only_when_a_deeper_tier_is_reached(self):
        state = {}
        self.assertFalse(is_new_trigger("x", 0, state))
        self.assertTrue(is_new_trigger("x", 1, state))
        record_notified("x", 1, state, "2026-11-01")
        self.assertFalse(is_new_trigger("x", 1, state))  # 同じ下落局面では再通知しない
        self.assertTrue(is_new_trigger("x", 2, state))   # より深いTierは通知する

    def test_rearm_needs_half_threshold_to_avoid_flicker(self):
        state = {"x": {"tier": 1, "date": "2026-11-01"}}
        rearm_if_recovered("x", 8.0, TIERS, state, ratio=0.5)  # Tier1=10 の半分(5)より深い → 解除しない
        self.assertEqual(notified_tier("x", state), 1)
        rearm_if_recovered("x", 4.9, TIERS, state, ratio=0.5)  # 半分より浅くなった
        self.assertEqual(notified_tier("x", state), 0)
        self.assertTrue(is_new_trigger("x", 1, state))          # 再び下がれば再通知

    def test_rearm_lowers_to_the_tier_still_reached(self):
        state = {"x": {"tier": 3, "date": "2026-11-01"}}
        rearm_if_recovered("x", 13.0, TIERS, state, ratio=0.5)  # Tier3=28 の半分(14)より浅く、Tier1(10)には到達
        self.assertEqual(notified_tier("x", state), 1)

    def test_load_triggered_migrates_old_formats(self):
        old = {
            "sox": [{"tier": 1, "date": None, "invested": True}, {"tier": 3, "date": "2026-11-09", "invested": True, "note": None, "recovered": False}],
            "orkan": [2],
            "fang": [],
            "new": {"tier": 1, "date": "2026-11-23"},
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "triggered.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            original = judge.TRIGGERED_FILE
            judge.TRIGGERED_FILE = path
            try:
                migrated = load_triggered()
            finally:
                judge.TRIGGERED_FILE = original
        self.assertEqual(migrated["sox"], {"tier": 3, "date": "2026-11-09"})
        self.assertEqual(migrated["orkan"], {"tier": 2, "date": None})
        self.assertNotIn("fang", migrated)
        self.assertEqual(migrated["new"], {"tier": 1, "date": "2026-11-23"})


class PeakTest(unittest.TestCase):
    def test_peak_starts_at_each_assets_first_purchase_date(self):
        baseline = {"dates": {"late": "2026-11-12"}, "prices": {}}
        peak, _ = update_peak({}, {"early": 100.0, "late": 50.0}, "2026-11-10", "2026-07-07", [], baseline)
        self.assertIn("early", peak)
        self.assertNotIn("late", peak)  # 起点より前は記録しない

    def test_seed_uses_history_and_known_purchase_prices(self):
        history = [{"date": "2026-08-01", "a": 120.0}, {"date": "2026-06-01", "a": 999.0}]  # 起点前は無視
        seed = seed_initial_peak(history, "a", "2026-07-07", 100.0, "2026-11-24", [("2026-01-06", 90.0), ("2026-10-13", 130.0)])
        self.assertEqual(seed, {"value": 130.0, "date": "2026-10-13"})

    def test_peak_rises_with_new_high_only(self):
        peak = {"a": {"value": 100.0, "date": "2026-08-01"}}
        update_peak(peak, {"a": 90.0}, "2026-09-01", "2026-07-07")
        self.assertEqual(peak["a"]["value"], 100.0)
        update_peak(peak, {"a": 110.0}, "2026-09-02", "2026-07-07")
        self.assertEqual(peak["a"], {"value": 110.0, "date": "2026-09-02"})


if __name__ == "__main__":
    unittest.main()
