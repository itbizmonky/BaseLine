"""
market_data.py
===============
市場心理指標（VIX・米10年国債利回り・USD/JPY）取得モジュール。

これらはあくまで参考情報であり、BUY/WAIT/HOLD/HIGHの判定（judge.py）には
一切使用しない。日経電子版の marketdata/quote ページから取得する。
取得失敗時は None を返し、呼び出し側（monitor.py）で前回値にフォールバックする。
"""

import json
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"
DATA_DIR = Path(__file__).parent.parent / "data"
MARKET_FILE = DATA_DIR / "market.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = 15

DEFAULT_SOURCES = {
    "vix": "https://www.nikkei.com/marketdata/quote/VIX/",
    "us10y": "https://www.nikkei.com/marketdata/quote/US10YT/",
    "usdjpy": "https://www.nikkei.com/marketdata/quote/USDJPY/",
}

DEFAULT_VIX_THRESHOLDS = {"overheated": 15, "normal": 20, "caution": 30, "fear": 40}


def load_settings() -> dict:
    """settings.json を読み込む"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_value(text: str) -> float | None:
    """
    日経電子版の指標値テキストを数値に変換する。
    USD/JPYのような「162.39-162.40」（Bid-Ask）形式は中間値を採用する。
    先頭が"-"の場合は単なる負の数なのでレンジとして扱わない。
    """
    text = text.replace(",", "").strip()
    try:
        if not text.startswith("-") and "-" in text:
            parts = text.split("-")
            if len(parts) == 2 and parts[0] and parts[1]:
                return round((float(parts[0]) + float(parts[1])) / 2, 4)
        return float(text)
    except ValueError:
        return None


def _fetch_nikkei_indicator(url: str, label: str) -> float | None:
    """日経電子版のmarketdata/quoteページから指標値を1つ取得する"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        el = soup.select_one('[class*="IndicatorSummary_value"]')
        if el is None:
            logger.warning(f"{label}: 価格要素が見つかりませんでした")
            return None
        val = _parse_value(el.text)
        if val is None:
            logger.warning(f"{label}: 値の解析に失敗しました ({el.text!r})")
            return None
        logger.info(f"{label}: {val}")
        return val
    except Exception as e:
        logger.warning(f"{label} 取得失敗: {e}")
        return None


def fetch_all_market_data(settings: dict | None = None) -> dict:
    """
    VIX・米10年金利・USD/JPYを取得する。
    取得失敗した指標は None になる（呼び出し側で前回値にフォールバックする）。

    Returns:
        {"vix": float|None, "us10y": float|None, "usdjpy": float|None}
    """
    settings = settings or load_settings()
    sources = settings.get("market_indicators", {}).get("sources", DEFAULT_SOURCES)

    return {
        "vix": _fetch_nikkei_indicator(sources.get("vix", DEFAULT_SOURCES["vix"]), "VIX"),
        "us10y": _fetch_nikkei_indicator(sources.get("us10y", DEFAULT_SOURCES["us10y"]), "米10年金利"),
        "usdjpy": _fetch_nikkei_indicator(sources.get("usdjpy", DEFAULT_SOURCES["usdjpy"]), "USD/JPY"),
    }


# ------------------------------------------------------------------
# データI/O（judge.pyのload_peak/save_peakと同じパターン）
# ------------------------------------------------------------------

def load_market() -> dict:
    """market.json を読み込む。ファイルがなければ空を返す。"""
    if not MARKET_FILE.exists():
        return {}
    with open(MARKET_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_market(market: dict) -> None:
    """market.json を保存する。"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(MARKET_FILE, "w", encoding="utf-8") as f:
        json.dump(market, f, ensure_ascii=False, indent=2)


def update_market(market: dict, fetched: dict, today_str: str) -> dict:
    """
    取得結果でmarket.jsonの内容を更新する（破壊的変更）。
    取得失敗（None）の指標は前回値・前回日付をそのまま維持する。
    """
    for key, value in fetched.items():
        if value is not None:
            market[key] = {"value": value, "date": today_str}
    return market


# ------------------------------------------------------------------
# 判定ロジック
# ------------------------------------------------------------------

def judge_vix_level(vix: float, thresholds: dict | None = None) -> dict:
    """
    VIXの水準を5段階で判定する。

    Returns:
        {"label": str, "css": str, "note": str}
    """
    t = thresholds or DEFAULT_VIX_THRESHOLDS
    if vix < t.get("overheated", 15):
        return {"label": "過熱", "css": "vix-hot", "note": "市場は楽観的で、警戒感がほとんどない状態です。"}
    elif vix < t.get("normal", 20):
        return {"label": "通常", "css": "vix-normal", "note": "市場は落ち着いた平常運転の状態です。"}
    elif vix < t.get("caution", 30):
        return {"label": "警戒", "css": "vix-caution", "note": "市場はやや神経質になっており、値動きが大きくなりやすい状態です。"}
    elif vix < t.get("fear", 40):
        return {"label": "恐怖", "css": "vix-fear", "note": "市場全体に強い不安が広がっており、全面的な下落が起きやすい状態です。"}
    else:
        return {"label": "暴落級", "css": "vix-crash", "note": "市場が極度のパニック状態です。歴史的な急落局面で見られる水準です。"}


def calc_direction(current: float | None, previous: float | None) -> dict:
    """
    前回値との差分・方向を計算する（米10年金利・USD/JPYの表示用）。

    Returns:
        {"diff": float|None, "arrow": "↗"|"↘"|"→"}
    """
    if current is None or previous is None:
        return {"diff": None, "arrow": "→"}
    diff = current - previous
    if diff > 0.001:
        arrow = "↗"
    elif diff < -0.001:
        arrow = "↘"
    else:
        arrow = "→"
    return {"diff": diff, "arrow": arrow}


# ------------------------------------------------------------------
# スタンドアローン実行（テスト用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("=== 市場心理指標 取得テスト ===")
    settings = load_settings()
    data = fetch_all_market_data(settings)

    print("\n【取得結果】")
    for key, val in data.items():
        print(f"  {key:8s}: {val if val is not None else '取得失敗'}")

    if data["vix"] is not None:
        vix_thresholds = settings.get("market_indicators", {}).get("vix_thresholds")
        level = judge_vix_level(data["vix"], vix_thresholds)
        print(f"\n  VIX判定: {level['label']} - {level['note']}")
