import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import judge
from judge import (
    calc_day_change, calc_drawdown, judge_tier, load_triggered, notified_tier,
    save_triggered, seed_initial_peak, update_alert_state, update_peak,
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

    def test_same_as_of_row_is_ignored_when_the_price_date_is_passed(self):
        rows = [{"date": "2026-11-20", "a": 110.0}, {"date": "2026-11-24", "a": 121.0}]   # 基準日キーの履歴。今日の基準日の行がある
        self.assertAlmostEqual(calc_day_change(rows, "a", 121.0, "2026-11-24"), 10.0)

    def test_none_when_no_price_or_no_history(self):
        self.assertIsNone(calc_day_change(self.ROWS, "a", None, "2026-11-24"))
        self.assertIsNone(calc_day_change([], "a", 100.0, "2026-11-24"))
        self.assertIsNone(calc_day_change(self.ROWS, "missing", 100.0, "2026-11-24"))


def run_path(thresholds, path, state=None, ratio=0.5):
    """下落率の列を1日ずつ流し、通知されたTierの列を返す（monitor.pyと同じ呼び方）。"""
    state = {} if state is None else state
    notes = []
    for day, dd in enumerate(path):
        tier = update_alert_state("x", dd, thresholds, state, ratio, f"d{day}")
        if tier:
            notes.append(tier)
    return notes, state


class AlertStateTest(unittest.TestCase):
    def test_notify_only_when_a_deeper_tier_is_reached(self):
        notes, _ = run_path(TIERS, [0, 5, 10, 12, 18, 20, 18])
        self.assertEqual(notes, [1, 2])  # 同じ下落局面では再通知しない。深いTierは通知する

    def test_rearm_needs_half_threshold_to_avoid_flicker(self):
        # Tier1=10。5（半分）以上にいる間は何度往復しても再通知しない
        notes, state = run_path(TIERS, [10, 8, 11, 6, 12, 5.5, 10])
        self.assertEqual(notes, [1])
        self.assertEqual(notified_tier("x", state), 1)
        # 半分(5)より浅くなると再武装され、再び下がれば再通知する
        notes, state = run_path(TIERS, [10, 4.9, 10])
        self.assertEqual(notes, [1, 1])

    def test_sox_example_does_not_renotify_tier2_before_it_is_rearmed(self):
        # 校正の定義（Tierごとの独立再武装）と一致すること。Tier2(33)は16.5より浅くなるまで通知済みのまま
        notes, state = run_path([16, 33, 45], [0, 10, 20, 35, 46, 40, 30, 20, 34, 40, 10, 0])
        self.assertEqual(notes, [1, 2, 3])
        # 末尾で全て再武装され、記録が消える
        self.assertNotIn("x", state)
        # その後の再下落は、また順に通知される
        notes2, _ = run_path([16, 33, 45], [0, 20, 35, 46], state)
        self.assertEqual(notes2, [1, 2, 3])

    def test_tiers_rearm_independently(self):
        # Tier3まで到達後、Tier3だけ再武装（< 14）しても、Tier1・2は通知済みのまま
        notes, state = run_path(TIERS, [10, 18, 28, 13])
        self.assertEqual(notes, [1, 2, 3])
        self.assertEqual(sorted(int(k) for k in state["x"]["notified"]), [1, 2])
        self.assertEqual(notified_tier("x", state), 2)

    def test_crossing_several_tiers_at_once_notifies_only_the_deepest_but_records_all(self):
        notes, state = run_path(TIERS, [0, 20])
        self.assertEqual(notes, [2])  # Tier1・2を同時に越えても1通（深いほう）
        self.assertEqual(sorted(int(k) for k in state["x"]["notified"]), [1, 2])
        self.assertEqual(state["x"]["tier"], 2)

    def test_no_thresholds_never_notifies(self):
        state = {}
        self.assertEqual(update_alert_state("x", 99.0, [], state, 0.5, "d"), 0)
        self.assertEqual(state, {})

    def test_legacy_record_is_initialised_from_current_drawdown_without_renotifying(self):
        # 旧形式 {"tier":1,"date"}: 下落率が半分以上 → 通知済みとして引き継ぎ、通知なし。効力日を記録
        state = {"x": {"tier": 1, "date": "2026-11-09"}}
        self.assertEqual(update_alert_state("x", 8.0, TIERS, state, 0.5, "2026-11-25"), 0)
        self.assertEqual(state["x"]["notified"], {"1": "2026-11-09"})
        self.assertEqual(state["x"]["migrated"], {"from": "v1", "date": "2026-11-25"})
        self.assertEqual(update_alert_state("x", 9.0, TIERS, state, 0.5, "2026-11-26"), 0)  # 以降も再通知しない
        self.assertEqual(state["x"]["migrated"]["date"], "2026-11-25")  # 効力日は変わらない

    def test_legacy_record_below_half_is_cleared_and_deeper_tier_notifies_only_that_tier(self):
        state = {"x": {"tier": 1, "date": "2026-11-09"}}
        self.assertEqual(update_alert_state("x", 3.0, TIERS, state, 0.5, "2026-11-25"), 0)
        self.assertNotIn("x", state)  # 半分より浅い → 再武装（記録削除）
        state = {"x": {"tier": 1, "date": "2026-11-09"}}
        self.assertEqual(update_alert_state("x", 19.0, TIERS, state, 0.5, "2026-11-25"), 2)  # Tier2だけ通知
        self.assertEqual(sorted(state["x"]["notified"]), ["1", "2"])

    def test_notified_state_round_trips_through_save_and_load(self):
        _, state = run_path(TIERS, [10, 18])
        with tempfile.TemporaryDirectory() as d:
            original_file, original_dir = judge.TRIGGERED_FILE, judge.DATA_DIR
            judge.TRIGGERED_FILE, judge.DATA_DIR = Path(d) / "triggered.json", Path(d)
            try:
                save_triggered(state)
                loaded = load_triggered()
            finally:
                judge.TRIGGERED_FILE, judge.DATA_DIR = original_file, original_dir
        self.assertEqual(loaded, state)
        notes, _ = run_path(TIERS, [18, 20], loaded)
        self.assertEqual(notes, [])  # 読み込み後も通知済みのまま

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

    def test_notification_path_sends_one_message_per_alert(self):
        # --dry-run は通知経路を通らないため、monitor.py と同じ流れで notify_tier_reached を実際に呼んで確認する
        import notify
        asset = {"name": "テスト銘柄", "short_name": "テスト"}
        state = {}
        with unittest.mock.patch.object(notify, "_send_flex_message", return_value=True) as send:
            for day, dd in enumerate([0, 20, 21, 25, 9, 20]):  # 20でTier1・2を同時に越える。以降は同じ局面
                tier = update_alert_state("x", dd, TIERS, state, 0.5, f"d{day}")
                if tier:
                    notify.notify_tier_reached(asset, tier, dd, -3.0, "https://example.com/")
        self.assertEqual(send.call_count, 1)  # 1通のみ。9に戻ってもTier1(5)・Tier2(9)は再武装されず、再び20でも再通知されない


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

    def test_peak_date_and_start_use_the_price_as_of_date(self):
        baseline = {"dates": {"x": "2026-11-12"}, "prices": {}}
        peak = {}
        # 実行日 11/12 でも、価格の基準日が 11/11（起点より前）なら記録しない
        update_peak(peak, {"x": 100.0}, "2026-11-12", "2026-07-07", [], baseline, None, {"x": "2026-11-11"})
        self.assertNotIn("x", peak)
        # 基準日が起点以降なら、最高値の日付は実行日ではなく基準日
        update_peak(peak, {"x": 100.0}, "2026-11-13", "2026-07-07", [], baseline, None, {"x": "2026-11-12"})
        self.assertEqual(peak["x"], {"value": 100.0, "date": "2026-11-12"})
        update_peak(peak, {"x": 110.0}, "2026-11-17", "2026-07-07", [], baseline, None, {"x": "2026-11-16"})
        self.assertEqual(peak["x"], {"value": 110.0, "date": "2026-11-16"})

    def test_peak_rises_with_new_high_only(self):
        peak = {"a": {"value": 100.0, "date": "2026-08-01"}}
        update_peak(peak, {"a": 90.0}, "2026-09-01", "2026-07-07")
        self.assertEqual(peak["a"]["value"], 100.0)
        update_peak(peak, {"a": 110.0}, "2026-09-02", "2026-07-07")
        self.assertEqual(peak["a"], {"value": 110.0, "date": "2026-09-02"})


if __name__ == "__main__":
    unittest.main()
