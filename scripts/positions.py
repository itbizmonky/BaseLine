"""
positions.py
=============
保有ポジション（Tier投資対象外）取得・判定モジュール。

「はじめてのNISA・全世界株式インデックス（オール・カントリー）」「テスラ」など、
階層的追加投資（Tier1/2/3）の対象ではなく、現在価格と取得単価の差（含み損益）を
監視・通知するだけの保有ポジションを扱う。

judge.py（Tier判定）・fetch_nav.py（4銘柄の基準価額取得）とは完全に分離しており、
このモジュールの取得失敗・不具合が既存のTier判定・LINE通知フローに影響することはない。
"""

import csv
import json
import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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

DEFAULT_GAIN_LOSS_THRESHOLDS = {"great": 10, "good": 0, "caution": -10, "warning": -20}


def load_settings() -> dict:
    """settings.json を読み込む"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------
# 取得
# ------------------------------------------------------------------

def fetch_nikkei_fund_price(fund_code: str, label: str) -> float | None:
    """日経電子版の投資信託ページから基準価格を取得する（fetch_nav.pyと同じパターン）"""
    url = f"https://www.nikkei.com/nkd/fund/?fcode={fund_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        el = soup.select_one(".m-stockPriceElm_value")
        if el is None:
            logger.warning(f"{label}: 価格要素 (.m-stockPriceElm_value) が見つかりませんでした")
            return None
        text = el.text.replace(",", "").replace("円", "").strip()
        val = float(text)
        logger.info(f"{label}: {val:,.0f}円 (日経)")
        return val
    except Exception as e:
        logger.warning(f"{label} 取得失敗: {e}")
        return None


def fetch_tesla_price(url: str = "https://finance.yahoo.co.jp/quote/TSLA") -> float | None:
    """
    Yahoo!ファイナンスの米国株ページからテスラの株価（USD）を取得する。
    表示は実勢に対し15分ディレイ。
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        el = soup.select_one('[class*="PriceBoard__price__"]')
        if el is None:
            logger.warning("テスラ: 価格要素が見つかりませんでした")
            return None
        text = el.text.strip().replace(",", "")
        m = re.match(r"-?\d+(\.\d+)?", text)
        if not m:
            logger.warning(f"テスラ: 値の解析に失敗しました ({text!r})")
            return None
        val = float(m.group(0))
        logger.info(f"テスラ: {val:.2f} USD (Yahoo!ファイナンス、15分ディレイ)")
        return val
    except Exception as e:
        logger.warning(f"テスラ 取得失敗: {e}")
        return None


def fetch_all_positions(settings: dict | None = None) -> dict:
    """
    settings.json の positions.items で定義された全ポジションを取得する。
    取得失敗した銘柄は None になる（呼び出し側で前回値にフォールバックする）。

    Returns:
        {position_id: float|None, ...}
    """
    settings = settings or load_settings()
    items = settings.get("positions", {}).get("items", [])
    results = {}
    for item in items:
        pid = item["id"]
        source = item.get("source")
        if source == "nikkei_fund":
            results[pid] = fetch_nikkei_fund_price(item["fund_code"], item.get("short_name", pid))
        elif source == "yahoo_us_stock":
            results[pid] = fetch_tesla_price()
        else:
            logger.warning(f"{pid}: 未知の取得元 source={source!r}")
            results[pid] = None
    return results


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


def update_positions(positions: dict, fetched: dict, today_str: str) -> dict:
    """
    取得結果でpositions.jsonの内容を更新する（破壊的変更）。
    取得失敗（None）の銘柄は前回値・前回日付をそのまま維持する。
    """
    for pid, value in fetched.items():
        if value is not None:
            positions[pid] = {"value": value, "date": today_str}
    return positions


def load_positions_history() -> list[dict]:
    """
    positions_history.csv を読み込み、辞書のリストで返す。
    列は settings.json の positions.items に応じて動的に変わる。
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


def append_positions_history(today_str: str, prices: dict, position_ids: list[str]) -> None:
    """
    今日の価格を positions_history.csv に追記する。
    同日付がすでにある場合は上書きしない（べき等。history.csvと同じ方式）。
    """
    DATA_DIR.mkdir(exist_ok=True)
    existing = load_positions_history()
    existing_dates = {r["date"] for r in existing}

    if today_str in existing_dates:
        logger.info(f"positions_history.csv: {today_str} はすでに存在するためスキップ")
        return

    fieldnames = ["date"] + position_ids
    file_exists = POSITIONS_HISTORY_FILE.exists()

    with open(POSITIONS_HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row = {"date": today_str}
        for pid in position_ids:
            val = prices.get(pid)
            row[pid] = f"{val:.2f}" if val is not None else ""
        writer.writerow(row)

    logger.info(f"positions_history.csv に {today_str} のデータを追記しました")


# ------------------------------------------------------------------
# 判定ロジック
# ------------------------------------------------------------------

def calc_gain_loss_ratio(current_price: float, cost_basis: float) -> float:
    """
    含み損益率を計算する（%）。通貨は取得単価と同じ通貨系で計算するため
    為替変動の影響を受けない（例: テスラはUSD同士で計算する）。
    """
    if cost_basis <= 0:
        return 0.0
    return (current_price - cost_basis) / cost_basis * 100


def judge_gain_loss_level(ratio: float, thresholds: dict | None = None) -> dict:
    """
    含み損益率を5段階で判定する。

    Returns:
        {"label": str, "css": str, "emoji": str}
    """
    t = thresholds or DEFAULT_GAIN_LOSS_THRESHOLDS
    if ratio >= t.get("great", 10):
        return {"label": "絶好調", "css": "pos-great", "emoji": "🟢"}
    elif ratio >= t.get("good", 0):
        return {"label": "順調", "css": "pos-good", "emoji": "🟢"}
    elif ratio >= t.get("caution", -10):
        return {"label": "様子見", "css": "pos-neutral", "emoji": "⚪"}
    elif ratio >= t.get("warning", -20):
        return {"label": "注意", "css": "pos-caution", "emoji": "🟡"}
    else:
        return {"label": "警戒", "css": "pos-warning", "emoji": "🔴"}


# ------------------------------------------------------------------
# スタンドアローン実行（テスト用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("=== 保有ポジション 取得テスト ===")
    settings = load_settings()
    fetched = fetch_all_positions(settings)

    print("\n【取得結果】")
    items = settings.get("positions", {}).get("items", [])
    thresholds = settings.get("positions", {}).get("gain_loss_thresholds")
    for item in items:
        pid = item["id"]
        val = fetched.get(pid)
        if val is None:
            print(f"  {item['short_name']}: 取得失敗")
            continue
        cost_basis = item.get("cost_basis", 0)
        ratio = calc_gain_loss_ratio(val, cost_basis)
        level = judge_gain_loss_level(ratio, thresholds)
        unit = "USD" if item.get("currency") == "USD" else "円"
        print(f"  {item['short_name']}: {val:,.2f}{unit} (取得単価 {cost_basis:,.2f}{unit}) 含み損益 {ratio:+.2f}% [{level['label']}]")
