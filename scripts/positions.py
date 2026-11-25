"""
positions.py
=============
米国個別株（NVDA・TSLAなど、日経の基準価額が取れない保有資産）の株価取得・履歴保存モジュール。

株価はYahoo Finance（米国）のチャートAPIから取得する（Yahoo!ファイナンスJPのページは、2026-10以降
価格をJavaScriptで後から描画する構造に変わり、HTMLから価格を取れなくなったため）。
最高値比・平均取得単価比はUSD基準で計算する（為替変動の影響を受けない）。

judge.py（Tier判定）・fetch_nav.py（投資信託の基準価額取得）とは分離しており、このモジュールの
取得失敗・不具合が投資信託のTier判定・LINE通知フローに影響することはない。
"""

import csv
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"
DATA_DIR = Path(__file__).parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
POSITIONS_HISTORY_FILE = DATA_DIR / "positions_history.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = 15
YAHOO_CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
USDJPY_SYMBOL = "JPY=X"


def load_settings() -> dict:
    """settings.json を読み込む"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------
# 取得
# ------------------------------------------------------------------

def _fetch_yahoo_chart(symbol: str, range_: str) -> dict | None:
    """Yahoo Financeのチャートの result[0]（meta・timestamp・indicators）を返す。取得失敗は None。"""
    last_error = None
    for host in YAHOO_CHART_HOSTS:
        url = f"https://{host}/v8/finance/chart/{symbol}?range={range_}&interval=1d"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            result = (resp.json().get("chart") or {}).get("result")
            if result:
                return result[0]
            last_error = "result が空です"
        except Exception as e:  # noqa: BLE001 取得元の障害は前回値で継続するため握りつぶさず記録する
            last_error = e
    logger.warning(f"{symbol} 取得失敗（Yahoo Finance）: {last_error}")
    return None


def _local_date(epoch: float, gmtoffset: int) -> date:
    """取引所のローカル日付（epoch秒＋UTCからのオフセット秒）。"""
    return (datetime.fromtimestamp(0, timezone.utc) + timedelta(seconds=epoch + gmtoffset)).date()


def parse_us_stock_quote(chart: dict, now: float | None = None) -> dict | None:
    """
    Yahoo Financeのチャート結果から、確定した直近の終値と取引日を取り出す。
    通常取引時間中（currentTradingPeriod.regular の開始〜終了の間）の regularMarketPrice は途中の価格で、
    終値ではないため使わず、日次の配列にある最後の確定済みの日（取引所の今日より前）の終値を返す。
    Returns: {"value": float, "as_of": date | None}。取れなければ None
    """
    meta = chart.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    now = time.time() if now is None else now
    regular = ((meta.get("currentTradingPeriod") or {}).get("regular")) or {}
    start, end = regular.get("start"), regular.get("end")
    market_open = start is not None and end is not None and start <= now < end

    if not market_open:
        price = meta.get("regularMarketPrice")
        if price is None:
            logger.warning("regularMarketPrice がありません")
            return None
        traded = meta.get("regularMarketTime")
        return {"value": float(price), "as_of": _local_date(traded, offset) if traded else None}

    today_local = _local_date(now, offset)
    stamps = chart.get("timestamp") or []
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for ts, close in reversed(list(zip(stamps, closes))):
        if close is not None and _local_date(ts, offset) < today_local:
            return {"value": float(close), "as_of": _local_date(ts, offset)}
    return None


def fetch_us_stock_quote(ticker: str, now: float | None = None) -> dict | None:
    """米国株の直近の確定した終値（USD）と取引日。取得失敗は None。日本時間の朝に実行すると前営業日（米国）の終値になる。"""
    chart = _fetch_yahoo_chart(ticker, "5d")
    if chart is None:
        return None
    quote = parse_us_stock_quote(chart, now)
    if quote is None:
        logger.warning(f"{ticker}: 確定した終値を取得できませんでした")
        return None
    logger.info(f"{ticker}: {quote['value']:.2f} USD (Yahoo Finance・取引日 {quote['as_of'] or '不明'})")
    return quote


def fetch_us_stock_price(ticker: str) -> float | None:
    """米国株の直近の確定した終値（USD）だけを返す。取引日が必要な場合は fetch_us_stock_quote()。"""
    quote = fetch_us_stock_quote(ticker)
    return quote["value"] if quote else None


def fetch_daily_closes(symbol: str, range_: str = "6mo") -> list[tuple[str, float]]:
    """日次の終値 [(YYYY-MM-DD, 終値)] を古い順に返す（履歴の補完用）。取得失敗は空リスト。"""
    chart = _fetch_yahoo_chart(symbol, range_)
    if chart is None:
        return []
    stamps = chart.get("timestamp") or []
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    out = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        out.append((datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), float(close)))
    return out


def fetch_all_position_quotes(settings: dict | None = None) -> dict:
    """
    settings.json の positions.items で定義された全ポジション（米国株）の終値と取引日を取得する。
    取得失敗した銘柄は None になる（呼び出し側で前回値にフォールバックする）。

    Returns:
        {position_id: {"value": float, "as_of": date | None} | None, ...}
    """
    settings = settings or load_settings()
    items = settings.get("positions", {}).get("items", [])
    results = {}
    for item in items:
        pid = item["id"]
        source = item.get("source")
        if source == "yahoo_us_stock":
            results[pid] = fetch_us_stock_quote(item["ticker"])
        else:
            logger.warning(f"{pid}: 未知の取得元 source={source!r}")
            results[pid] = None
    return results


def fetch_all_positions(settings: dict | None = None) -> dict:
    """全ポジションの終値だけを返す（{position_id: float | None}）。取引日が必要な場合は fetch_all_position_quotes()。"""
    return {pid: (q["value"] if q else None) for pid, q in fetch_all_position_quotes(settings).items()}


# ------------------------------------------------------------------
# データI/O（judge.pyのload_peak/save_peak, market_data.pyのload_market/save_marketと同じパターン）
# ------------------------------------------------------------------

def load_positions() -> dict:
    """positions.json を読み込む。ファイルがなければ空を返す。"""
    if not POSITIONS_FILE.exists():
        return {}
    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_positions(positions: dict) -> None:
    """positions.json を保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def update_positions(positions: dict, fetched: dict, today_str: str, as_of: dict | None = None) -> dict:
    """
    取得結果でpositions.jsonの内容を更新する（破壊的変更）。
    取得に失敗した銘柄（None）は更新せず、前回値と前回の基準日を維持する。
    date は価格の基準日（取引日。不明なら実行日）、fetched は取得した実行日。
    """
    for pid, value in fetched.items():
        if value is not None:
            traded = (as_of or {}).get(pid)
            positions[pid] = {"value": value, "date": traded.isoformat() if traded else today_str, "fetched": today_str}
    return positions


def load_positions_history() -> list[dict]:
    """
    positions_history.csv を読み込み、辞書のリストで返す。
    列は米国株の資産ID（tesla, nvda など）と usdjpy（円換算用のその日のレート）。
    """
    if not POSITIONS_HISTORY_FILE.exists():
        return []
    rows = []
    with open(POSITIONS_HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {"date": row["date"]}
            for key, val in row.items():
                if key == "date":
                    continue
                try:
                    parsed[key] = float(val) if val else None
                except ValueError:
                    parsed[key] = None
            rows.append(parsed)
    return rows


def append_positions_history(today_str: str, prices: dict, columns: list[str]) -> None:
    """
    今日の価格を positions_history.csv に追記する。
    同日付がすでにある場合は上書きしない（べき等。history.csvと同じ方式）。
    columns に既存ヘッダにない列（新規追加の米国株・usdjpy）があれば、ヘッダに列を追加して
    既存行を空欄で補完する（列ずれ防止。judge.append_history() と同じ方式）。
    """
    DATA_DIR.mkdir(exist_ok=True)
    existing = load_positions_history()
    existing_dates = {r["date"] for r in existing}

    if today_str in existing_dates:
        logger.info(f"positions_history.csv: {today_str} はすでに存在するためスキップ")
        return

    header = []
    if POSITIONS_HISTORY_FILE.exists():
        with open(POSITIONS_HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
    position_columns = [c for c in header if c != "date"]
    for pid in columns:
        if pid not in position_columns:
            position_columns.append(pid)
    fieldnames = ["date"] + position_columns

    if header and header != fieldnames:
        with open(POSITIONS_HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
            raw_rows = list(csv.DictReader(f))
        with open(POSITIONS_HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
            migrated = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
            migrated.writeheader()
            migrated.writerows(raw_rows)
        logger.info(f"positions_history.csv のヘッダを移行しました: {header} → {fieldnames}")

    file_exists = POSITIONS_HISTORY_FILE.exists()

    with open(POSITIONS_HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        if not file_exists:
            writer.writeheader()
        row = {"date": today_str}
        for pid in position_columns:
            val = prices.get(pid)
            row[pid] = f"{val:.2f}" if val is not None else ""
        writer.writerow(row)

    logger.info(f"positions_history.csv に {today_str} のデータを追記しました")


# ------------------------------------------------------------------
# スタンドアローン実行（テスト用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("=== 米国株 取得テスト ===")
    settings = load_settings()
    fetched = fetch_all_position_quotes(settings)

    print("\n【取得結果】")
    for item in settings.get("positions", {}).get("items", []):
        q = fetched.get(item["id"])
        print(f"  {item['short_name']}: " + (f"{q['value']:,.2f} USD  取引日 {q['as_of'] or '不明'}" if q else "取得失敗"))
