import json
import tempfile
import unittest
from pathlib import Path

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import backfill_history as bf
import history_store


class FillTest(unittest.TestCase):
    FMT = staticmethod(lambda c, v: f"{v:.0f}")

    def test_fills_only_blank_cells_inside_the_window_and_reports_differences(self):
        existing = {"2026-07-07": {"date": "2026-07-07", "a": "10", "b": ""}, "2026-07-09": {"date": "2026-07-09", "a": "11"}}
        incoming = {"2026-07-07": {"a": 10.0, "b": 20.0}, "2026-07-08": {"a": 15.0}, "2026-07-09": {"a": 12.0}, "2026-06-01": {"a": 1.0}, "2026-12-01": {"a": 9.0}}
        rows, conflicts = bf.build_fill(existing, incoming, "2026-07-07", "2026-11-24", self.FMT)
        self.assertEqual(rows, {"2026-07-07": {"b": "20"}, "2026-07-08": {"a": "15"}})
        self.assertEqual(conflicts, [("2026-07-09", "a", "11", "12")])

    def test_peak_is_raised_from_the_start_date_and_never_lowered(self):
        peak = {"x": {"value": 41703.0, "date": "2026-10-07"}, "y": {"value": 500.0, "date": "2026-10-01"}, "z": {"value": 175.0, "date": "2026-11-12"}}
        series = {"x": {"2025-12-01": 99999.0, "2026-01-06": 40000.0, "2026-08-14": 41791.0, "2026-10-07": 41703.0},
                  "y": {"2026-10-01": 400.0}, "z": {"2026-11-12": 170.0}}
        starts = {"x": "2026-01-06", "y": "2026-07-07", "z": "2026-11-12"}
        changes = bf.raise_peaks(peak, series, starts, "2026-11-24")
        self.assertEqual(peak["x"], {"value": 41791.0, "date": "2026-08-14"})   # 起点日より前の値は使わない
        self.assertEqual(peak["y"]["value"], 500.0)                              # 下げない（約定単価など履歴に無い価格を守る）
        self.assertEqual(peak["z"]["value"], 175.0)
        self.assertEqual([c["asset"] for c in changes], ["x"])

    def test_usdjpy_uses_the_next_days_rate_like_the_existing_rows(self):
        fx = {"2026-08-10": 159.26, "2026-08-11": 159.33, "2026-08-12": 159.43}
        self.assertEqual(bf.next_day_rate(fx, "2026-08-10"), 159.33)
        self.assertIsNone(bf.next_day_rate(fx, "2026-08-12"))


class RunTest(unittest.TestCase):
    def test_dry_run_changes_nothing_and_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text(json.dumps({
                "peak_start_date": "2026-07-07", "baseline": {"dates": {"f2": "2026-01-06"}},
                "funds": [{"id": "f1", "fund_code": "x"}, {"id": "f2", "fund_code": "y"}],
                "positions": {"items": [{"id": "s1", "ticker": "S1"}]}}), encoding="utf-8")
            (root / "data" / "peak.json").write_text(json.dumps({"f2": {"value": 90.0, "date": "2026-10-07"}}), encoding="utf-8")
            history_store.write_rows(root / "data" / "history.csv", ["date", "f1", "f2"],
                                     [{"date": "2026-07-07", "f1": "100", "f2": ""}, {"date": "2026-07-10", "f1": "103", "f2": "93"}, {"date": "2026-08-14", "f1": "104", "f2": "95"}])
            history_store.write_rows(root / "data" / "positions_history.csv", ["date", "s1", "usdjpy"],
                                     [{"date": "2026-07-09", "s1": "50.00", "usdjpy": "150.00"}])
            quick = {"f1": {"2026-07-07": 100.0, "2026-07-08": 101.0}, "f2": {"2026-01-06": 80.0, "2026-07-07": 91.0, "2026-07-10": 93.0, "2026-08-14": 95.0}}
            closes = {"s1": {"2026-07-07": 48.0, "2026-07-09": 50.0}}
            fx = {"2026-07-07": 149.0, "2026-07-08": 149.5, "2026-07-09": 150.0, "2026-07-10": 150.5}
            before = (root / "data" / "history.csv").read_bytes()
            self.assertEqual(bf.run(root, False, quick, closes, fx), 0)
            self.assertEqual((root / "data" / "history.csv").read_bytes(), before)       # dry-run は書かない
            bf.run(root, True, quick, closes, fx)
            h = {r["date"]: r for r in history_store.read_rows(root / "data" / "history.csv")[1]}
            self.assertEqual((h["2026-07-07"]["f2"], h["2026-07-08"]["f1"]), ("91", "101"))
            p = {r["date"]: r for r in history_store.read_rows(root / "data" / "positions_history.csv")[1]}
            self.assertEqual((p["2026-07-07"]["s1"], p["2026-07-07"]["usdjpy"]), ("48.00", "149.50"))
            self.assertEqual(p["2026-07-09"]["usdjpy"], "150.00")                          # 既存の値は変えない
            peak = json.loads((root / "data" / "peak.json").read_text(encoding="utf-8"))
            self.assertEqual(peak["f2"], {"value": 95.0, "date": "2026-08-14"})
            self.assertNotIn("f1", peak)                                                  # 記録の無い資産は作らない
            after = (root / "data" / "history.csv").read_bytes()
            bf.run(root, True, quick, closes, fx)
            self.assertEqual((root / "data" / "history.csv").read_bytes(), after)          # 2回目は変化なし


if __name__ == "__main__":
    unittest.main()
