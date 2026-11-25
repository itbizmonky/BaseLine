import unittest
from datetime import date
from unittest import mock

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

from bs4 import BeautifulSoup

import fetch_nav
import monitor
import notify
import positions
from freshness import (
    assess, classify_prices, parse_nikkei_asof, prev_weekday, short_date, usable_values, weekday_lag,
)


class LagTest(unittest.TestCase):
    def test_prev_weekday_skips_weekends(self):
        self.assertEqual(prev_weekday(date(2026, 11, 23)), date(2026, 11, 20))   # 月曜の前の平日＝金曜
        self.assertEqual(prev_weekday(date(2026, 11, 25)), date(2026, 11, 24))

    def test_lag_is_zero_when_the_latest_business_day_is_reported(self):
        self.assertEqual(weekday_lag(date(2026, 11, 20), date(2026, 11, 23)), 0)   # 月曜の実行・基準日は金曜
        self.assertEqual(weekday_lag(date(2026, 11, 24), date(2026, 11, 25)), 0)

    def test_holiday_makes_the_lag_one(self):
        # 11/23(月)が祝日 → 11/24(火)の実行でも基準日は金曜 11/20。直近の平日(11/23)より1平日遅れ
        self.assertEqual(weekday_lag(date(2026, 11, 20), date(2026, 11, 24)), 1)

    def test_status_by_tolerance(self):
        today = date(2026, 5, 7)   # GW明け（木）。直近の平日は 5/6
        self.assertEqual(assess(date(2026, 5, 1), today, 3)["status"], "fresh")    # 5/4,5/5,5/6 → lag 3
        self.assertEqual(assess(date(2026, 4, 30), today, 3)["status"], "stale")   # lag 4
        self.assertEqual(assess(None, today, 3)["status"], "unknown")
        self.assertEqual(assess(date(2026, 5, 6), today, 3, forced_stale=True)["status"], "stale")   # 取得失敗の前回値


class ParseTest(unittest.TestCase):
    def test_nikkei_page_text(self):
        self.assertEqual(parse_nikkei_asof("基準価格(11/24)： 42,418 円", date(2026, 11, 25)), date(2026, 11, 24))
        self.assertEqual(parse_nikkei_asof("基準価格（11/24）： 42,418 円", date(2026, 11, 25)), date(2026, 11, 24))
        self.assertIsNone(parse_nikkei_asof("価格", date(2026, 11, 25)))
        self.assertIsNone(parse_nikkei_asof("基準価格(2/30)", date(2026, 11, 25)))

    def test_year_rolls_back_in_january(self):
        self.assertEqual(parse_nikkei_asof("基準価格(12/30)： 1 円", date(2027, 1, 4)), date(2026, 12, 30))

    def test_extract_nav_reads_value_and_date_from_the_page_structure(self):
        html = '<dl><dt>基準価格(11/24)：</dt><dd><span class="m-stockPriceElm_value">42,418</span>円</dd></dl>'
        value, as_of = fetch_nav._extract_nav(BeautifulSoup(html, "lxml"), date(2026, 11, 25))
        self.assertEqual((value, as_of), (42418.0, date(2026, 11, 24)))
        nodate = '<div><span class="m-stockPriceElm_value">1,000</span></div>'
        self.assertEqual(fetch_nav._extract_nav(BeautifulSoup(nodate, "lxml"), date(2026, 11, 25)), (1000.0, None))
        self.assertIsNone(fetch_nav._extract_nav(BeautifulSoup("<p>x</p>", "lxml"), date(2026, 11, 25)))

    def test_short_date(self):
        self.assertEqual(short_date(date(2026, 11, 24)), "11/24")
        self.assertEqual(short_date("2026-11-04"), "11/4")
        self.assertEqual(short_date(None), "日付不明")


# 2026-11-25 の実データに近い形（EST。regular = 14:30〜21:00 UTC）
REGULAR_START, REGULAR_END = 1795530600, 1795554000


def chart(close_price=155.38, traded=REGULAR_END + 1):
    day1, day2 = REGULAR_START - 86400, REGULAR_START
    return {
        "meta": {"regularMarketPrice": close_price, "regularMarketTime": traded, "gmtoffset": -18000,
                 "currentTradingPeriod": {"regular": {"start": REGULAR_START, "end": REGULAR_END}}},
        "timestamp": [day1, day2],
        "indicators": {"quote": [{"close": [165.05, close_price]}]},
    }


class UsQuoteTest(unittest.TestCase):
    def test_after_the_close_uses_the_closing_price_and_trade_date(self):
        q = positions.parse_us_stock_quote(chart(), now=REGULAR_END + 3600)
        self.assertEqual(q, {"value": 155.38, "as_of": date(2026, 11, 24)})

    def test_during_the_session_ignores_the_intraday_price(self):
        c = chart(close_price=170.0, traded=REGULAR_START + 3600)   # 場中の価格 170（まだ終値ではない）
        q = positions.parse_us_stock_quote(c, now=REGULAR_START + 3600)
        self.assertEqual(q, {"value": 165.05, "as_of": date(2026, 11, 23)})   # 前日の確定済みの終値

    def test_no_completed_close_returns_none(self):
        c = chart(); c["timestamp"] = [REGULAR_START]; c["indicators"]["quote"][0]["close"] = [155.0]
        self.assertIsNone(positions.parse_us_stock_quote(c, now=REGULAR_START + 3600))


class GateTest(unittest.TestCase):
    TODAY = date(2026, 11, 25)

    def test_classify_and_usable_values_exclude_stale_and_missing(self):
        info = classify_prices({
            "ok": {"value": 100.0, "as_of": date(2026, 11, 24)},
            "old": {"value": 90.0, "as_of": date(2026, 11, 18)},                  # 4平日遅れ
            "failed": None,
            "fallback": {"value": 80.0, "as_of": date(2026, 11, 24), "forced_stale": True},
            "nodate": {"value": 70.0, "as_of": None},
        }, self.TODAY, 3)
        self.assertEqual({k: (v or {}).get("status") for k, v in info.items()},
                         {"ok": "fresh", "old": "stale", "failed": None, "fallback": "stale", "nodate": "unknown"})
        self.assertEqual(usable_values(info), {"ok": 100.0, "nodate": 70.0})

    def test_fetch_stocks_does_not_pass_stale_prices_to_peak_or_history(self):
        settings = helpers.load_settings()
        fetched = {"tesla": {"value": 380.0, "as_of": date(2026, 11, 24)},
                   "nvda": {"value": 150.0, "as_of": date(2026, 11, 17)}}          # NVDAは取引日が古い
        saved, history = {}, []
        with mock.patch.object(monitor, "fetch_all_position_quotes", return_value=fetched), \
             mock.patch.object(monitor, "load_positions", return_value={}), \
             mock.patch.object(monitor, "save_positions", side_effect=lambda p: saved.update(p)), \
             mock.patch.object(monitor, "append_positions_history", side_effect=lambda by_as_of, usdjpy, fetched_on: history.append(by_as_of)):
            usable, quotes = monitor.fetch_stocks(settings, "2026-11-25", 159.0, 3)
        self.assertEqual(usable, {"tesla": 380.0})
        self.assertFalse(quotes["tesla"]["stale"])
        self.assertTrue(quotes["nvda"]["stale"])
        self.assertEqual(quotes["nvda"]["date"], "2026-11-17")
        self.assertEqual(history, [{"2026-11-24": {"tesla": 380.0}}])   # 取引日をキーに、判定に使える価格だけ追記（NVDAは追記しない）
        self.assertEqual(saved["nvda"]["date"], "2026-11-17")   # positions.json の date は基準日
        self.assertEqual(saved["nvda"]["fetched"], "2026-11-25")

    def test_failed_fetch_falls_back_to_the_old_value_as_stale(self):
        settings = helpers.load_settings()
        old = {"tesla": {"value": 380.0, "date": "2026-11-24"}, "nvda": {"value": 150.0, "date": "2026-11-24"}}
        with mock.patch.object(monitor, "fetch_all_position_quotes", return_value={"tesla": None, "nvda": None}), \
             mock.patch.object(monitor, "load_positions", return_value=old), \
             mock.patch.object(monitor, "save_positions"), mock.patch.object(monitor, "append_positions_history"):
            usable, quotes = monitor.fetch_stocks(settings, "2026-11-25", 159.0, 3)
        self.assertEqual(usable, {})
        self.assertTrue(quotes["tesla"]["stale"] and quotes["nvda"]["stale"])

    def test_stale_notice_is_sent_with_the_base_date(self):
        # --dry-run は通知経路を通らないため、実際に notify_fetch_error を呼んで確認する
        names = ["エヌビディア（基準日11/17・判定保留）"]
        with mock.patch.object(notify, "_send_line_message", return_value=True) as send:
            notify.notify_fetch_error(names, "2026-11-25", "https://example.com/")
        self.assertIn("エヌビディア（基準日11/17・判定保留）", send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
