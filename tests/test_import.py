import tempfile
import unittest
from pathlib import Path

try:
    from tests import helpers  # noqa: F401  （リポジトリのルートから実行したとき）
except ImportError:
    import helpers  # noqa: F401  （tests/ を起点に実行したとき）

import import_sbi_history as imp
from import_sbi_history import KIND_FUND, KIND_US_STOCK, build_records, merge_into, parse_number, read_csv_table

SETTINGS = helpers.load_settings()
CFG = SETTINGS["sbi_import"]
CARD = SETTINGS["monthly_schedule"]["card"]

FUND_CSV = (
    "約定履歴\n\n"
    '商品指定,約定開始年月日,約定終了年月日,明細数,明細指定開始,明細指定終了\n"投資信託","2026年01月01日","2026年11月24日",3,1,3\n\n'
    "約定日,銘柄,銘柄コード,市場,取引,期限,預り,課税,約定数量,約定単価,手数料/諸経費等,税額,受渡日,受渡金額\n"
    '"2026/11/11","ニッセイＮＡＳＤＡＱ１００インデックスファンド＜購入・換金手数料なし＞","未設定","未設定","投信金額買付","-","NISA (成長)","-","7607","26293",0,0,"2026/11/16",20000\n'
    '"2026/11/10","ニッセイＮＡＳＤＡＱ１００インデックスファンド＜購入・換金手数料なし＞","未設定","未設定","投信金額買付","-","NISA (成長)","-","7596","26330",0,0,"2026/11/13",20000\n'
    '"2026/10/27","はじめてのＮＩＳＡ・全世界株式インデックス（オール・カントリー）","未設定","未設定","投信金額買付","-","NISA (成長)","-","38636","19412",0,0,"2026/11/04",75000\n'
)
# 2027-01の約定（2026-11-26に決定した配分。カード1/10: オルカン50,000のみ／現金1/23: NASDAQ100 40,000・Tracers 18,824）
JAN2027_CSV = (
    "約定履歴\n\n"
    '商品指定,約定開始年月日,約定終了年月日,明細数,明細指定開始,明細指定終了\n"投資信託","2027年01月01日","2027年01月31日",4,1,4\n\n'
    "約定日,銘柄,銘柄コード,市場,取引,期限,預り,課税,約定数量,約定単価,手数料/諸経費等,税額,受渡日,受渡金額\n"
    '"2027/01/12","ｅＭＡＸＩＳ　Ｓｌｉｍ　全世界株式（オール・カントリー）","未設定","未設定","投信金額買付","-","NISA (つみたて)","-","13000","38000",0,0,"2027/01/15",50000\n'
    '"2027/01/12","ニッセイＮＡＳＤＡＱ１００インデックスファンド＜購入・換金手数料なし＞","未設定","未設定","投信金額買付","-","NISA (成長)","-","3800","26000",0,0,"2027/01/15",10000\n'
    '"2027/01/26","ニッセイＮＡＳＤＡＱ１００インデックスファンド＜購入・換金手数料なし＞","未設定","未設定","投信金額買付","-","NISA (成長)","-","15000","26500",0,0,"2027/01/29",40000\n'
    '"2027/01/26","Ｔｒａｃｅｒｓ　Ｓ＆Ｐ５００トップ１０インデックス","未設定","未設定","投信金額買付","-","NISA (成長)","-","11000","17000",0,0,"2027/01/29",18824\n'
)
US_CSV = (
    '\n"約定履歴"\n\n"商品指定","約定開始年月日","約定終了年月日"\n"すべて","2025年11月26日","2026年11月25日"\n\n'
    '"国内約定日","銘柄","銘柄コード","市場","商品区分","注文種別","取引","預り区分","約定数量","約定単価","国内受渡日","受渡金額/決済損益"\n'
    '"2026/08/07","テスラ","TSLA","NASDAQ","米国株式","指値","現買","NISA","1","317.0100USD","2026/08/12","50,347円"\n'
    '"2026/08/07","テスラ","TSLA","NASDAQ","米国株式","指値","現買","NISA","1","317.0100USD","2026/08/12","50,347円"\n'
)


def write_tmp(content: str, encoding: str) -> Path:
    f = tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False)
    f.write(content.encode(encoding))
    f.close()
    return Path(f.name)


class ParseTest(unittest.TestCase):
    def test_parse_number_handles_units_and_commas(self):
        self.assertEqual(parse_number("24,098円"), 24098)
        self.assertEqual(parse_number("151.0000USD"), 151.0)
        self.assertEqual(parse_number("26293"), 26293)
        self.assertIsNone(parse_number("-"))

    def test_reads_both_csv_formats_and_encodings(self):
        kind, rows = read_csv_table(write_tmp(FUND_CSV, "utf-8-sig"))
        self.assertEqual((kind, len(rows)), (KIND_FUND, 3))
        kind, rows = read_csv_table(write_tmp(US_CSV, "cp932"))
        self.assertEqual((kind, len(rows)), (KIND_US_STOCK, 2))
        self.assertEqual(rows[0]["銘柄コード"], "TSLA")


class BuildRecordsTest(unittest.TestCase):
    def fund_records(self):
        _, rows = read_csv_table(write_tmp(FUND_CSV, "utf-8-sig"))
        recs, unmapped, warnings = build_records(rows, KIND_FUND, CFG, CARD)
        return recs, warnings

    def test_same_month_same_amount_marks_latest_as_card(self):
        recs, warnings = self.fund_records()
        nas = {r["date"]: r for a, r in recs if a == "nasdaq100"}
        self.assertEqual(nas["2026-11-11"]["account"], "side")   # カード積立（日付が最も遅い）
        self.assertEqual(nas["2026-11-10"]["account"], "attack")  # 手動の金額買
        self.assertTrue(any("要確認" in w for w in warnings))

    def test_child_fund_is_separate_account(self):
        recs, _ = self.fund_records()
        kids = [r for a, r in recs if a == "newnisa_orkan"][0]
        self.assertEqual((kids["account"], kids["frame"]), ("child", "growth"))

    def test_us_stock_keeps_usd_price_and_both_identical_rows(self):
        _, rows = read_csv_table(write_tmp(US_CSV, "cp932"))
        recs, _, _ = build_records(rows, KIND_US_STOCK, CFG, CARD)
        self.assertEqual(len(recs), 2)  # 同日・同金額の2件を落とさない
        asset, rec = recs[0]
        self.assertEqual(asset, "tesla")
        self.assertEqual((rec["currency"], rec["price"], rec["units"], rec["amount"], rec["frame"]), ("USD", 317.01, 1, 50347, "growth"))


class ScheduleFromJanuary2027Test(unittest.TestCase):
    """積立配分の変更（2026-11-26決定）後の約定を、カード積立（side）と原資からの買付（attack）に正しく分けられること。"""

    def test_card_orkan_is_side_cash_purchases_are_attack(self):
        _, rows = read_csv_table(write_tmp(JAN2027_CSV, "utf-8-sig"))
        recs, unmapped, _ = build_records(rows, KIND_FUND, CFG, CARD)
        self.assertEqual(unmapped, [])
        got = {(a, r["amount"]): r["account"] for a, r in recs}
        self.assertEqual(got[("orkan", 50000)], "side")            # カード1/10: オルカン50,000（つみたて枠）
        self.assertEqual(got[("nasdaq100", 40000)], "attack")      # 現金1/23: NASDAQ100 40,000
        self.assertEqual(got[("tracers", 18824)], "attack")        # 現金1/23: Tracers 18,824
        # カード設定からNASDAQ100は無くなったため、同額（10,000円）でもカード積立とは判定しない
        self.assertEqual(got[("nasdaq100", 10000)], "attack")


class MergeTest(unittest.TestCase):
    def test_count_matching_adds_only_missing_rows(self):
        rec = {"date": "2026-08-07", "price": 317.01, "units": 1, "amount": 50347, "currency": "USD", "account": "attack", "frame": "growth", "category": "追加買付"}
        history = {"tesla": [{"date": "2026-08-07", "amount": 50347}]}  # 既に1件ある
        result = merge_into(history, [("tesla", dict(rec)), ("tesla", dict(rec))])
        self.assertEqual((len(result["added"]), result["duplicates"]), (1, 1))
        self.assertEqual(len(history["tesla"]), 2)
        # 既存の1件に口数・単価・枠が補完される
        self.assertEqual(history["tesla"][0]["units"], 1)
        self.assertEqual(history["tesla"][0]["frame"], "growth")

    def test_rerun_is_idempotent(self):
        rec = {"date": "2026-11-10", "price": 26330, "units": 7596, "amount": 20000, "account": "attack", "frame": "growth", "category": "追加買付"}
        history = {}
        merge_into(history, [("nasdaq100", dict(rec))])
        again = merge_into(history, [("nasdaq100", dict(rec))])
        self.assertEqual((len(again["added"]), again["enriched"]), (0, 0))

    def test_reclassify_updates_account_and_generic_category_only(self):
        history = {"x": [
            {"date": "2026-01-01", "amount": 1, "account": "side", "category": "別枠積立"},
            {"date": "2026-01-02", "amount": 2, "account": "side", "category": "Tier1"},
        ]}
        new = [("x", {"date": "2026-01-01", "amount": 1, "account": "attack", "category": "追加買付"}),
               ("x", {"date": "2026-01-02", "amount": 2, "account": "attack", "category": "追加買付"})]
        merge_into(history, new, reclassify=True)
        self.assertEqual((history["x"][0]["account"], history["x"][0]["category"]), ("attack", "追加買付"))
        self.assertEqual((history["x"][1]["account"], history["x"][1]["category"]), ("attack", "Tier1"))  # 手書きの区分は残す


if __name__ == "__main__":
    unittest.main()
