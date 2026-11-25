import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import history_store
import judge
import positions
from history_store import as_of_key, read_rows, upsert_rows


def read_dicts(path):
    return read_rows(path)[1]


class UpsertTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "h.csv"
        self.corr = Path(self.dir.name) / "corrections.csv"

    def tearDown(self):
        self.dir.cleanup()

    def up(self, rows, **kw):
        return upsert_rows(self.path, rows, "2026-11-25", corrections_path=self.corr, **kw)

    def test_creates_the_file_and_is_idempotent_for_the_same_as_of_and_value(self):
        self.up({"2026-11-24": {"a": "100", "b": "200"}})
        before = self.path.read_bytes()
        result = self.up({"2026-11-24": {"a": "100", "b": "200"}})   # 祝日・未更新の日の再実行
        self.assertEqual(result, {"added": [], "filled": [], "conflicts": []})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(read_dicts(self.path)), 1)
        self.assertFalse(self.corr.exists())

    def test_different_value_is_not_overwritten_and_is_logged(self):
        self.up({"2026-11-24": {"a": "100"}})
        result = self.up({"2026-11-24": {"a": "101"}})
        self.assertEqual(result["conflicts"], [("2026-11-24", "a")])
        self.assertEqual(read_dicts(self.path)[0]["a"], "100")
        logged = list(csv.reader(open(self.corr, encoding="utf-8")))
        self.assertEqual(logged[0], history_store.CORRECTIONS_HEADER)
        self.assertEqual(logged[1][:4], ["2026-11-24", "a", "100", "101"])

    def test_fills_blank_cells_and_adds_new_columns_to_existing_rows(self):
        self.up({"2026-11-20": {"a": "1"}, "2026-11-24": {"a": "2"}})
        self.up({"2026-11-24": {"a": "2", "b": "9"}})
        rows = {r["date"]: r for r in read_dicts(self.path)}
        self.assertEqual(rows["2026-11-24"]["b"], "9")
        self.assertEqual(rows["2026-11-20"]["b"], "")   # 既存の行は空欄で補完（列ずれなし）

    def test_rows_stay_in_date_order(self):
        self.up({"2026-11-24": {"a": "2"}})
        self.up({"2026-11-20": {"a": "1"}})
        self.assertEqual([r["date"] for r in read_dicts(self.path)], ["2026-11-20", "2026-11-24"])

    def test_fill_only_column_is_never_logged_as_a_conflict(self):
        self.up({"2026-11-24": {"a": "2", "usdjpy": "159.00"}}, fill_only=("usdjpy",))
        result = self.up({"2026-11-24": {"a": "2", "usdjpy": "159.50"}}, fill_only=("usdjpy",))
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(read_dicts(self.path)[0]["usdjpy"], "159.00")

    def test_as_of_key_falls_back_to_the_given_day(self):
        self.assertEqual(as_of_key(date(2026, 11, 24), date(2026, 11, 20)), "2026-11-24")
        self.assertEqual(as_of_key(None, date(2026, 11, 20)), "2026-11-20")


class AppendHelpersTest(unittest.TestCase):
    def test_append_history_and_positions_use_the_as_of_date_as_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            old = (judge.HISTORY_FILE, positions.POSITIONS_HISTORY_FILE, history_store.CORRECTIONS_FILE)
            judge.HISTORY_FILE, positions.POSITIONS_HISTORY_FILE = Path(d) / "history.csv", Path(d) / "positions_history.csv"
            history_store.CORRECTIONS_FILE = Path(d) / "corrections.csv"
            try:
                judge.append_history({"2026-11-24": {"fang": 87932.0, "sox": 36573.0}}, "2026-11-25")
                judge.append_history({"2026-11-24": {"fang": 87932.0, "sox": 36573.0}}, "2026-11-26")   # 翌日の再実行（未更新）
                positions.append_positions_history({"2026-11-24": {"tesla": 384.68}}, 159.28, "2026-11-25")
                positions.append_positions_history({"2026-11-24": {"tesla": 384.68}}, 160.00, "2026-11-26")
                h = read_dicts(judge.HISTORY_FILE)
                p = read_dicts(positions.POSITIONS_HISTORY_FILE)
            finally:
                judge.HISTORY_FILE, positions.POSITIONS_HISTORY_FILE, history_store.CORRECTIONS_FILE = old
        self.assertEqual([r["date"] for r in h], ["2026-11-24"])
        self.assertEqual(h[0]["fang"], "87932")
        self.assertEqual([(r["date"], r["tesla"], r["usdjpy"]) for r in p], [("2026-11-24", "384.68", "159.28")])


if __name__ == "__main__":
    unittest.main()
