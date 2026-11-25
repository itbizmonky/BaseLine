"""
fetch_nav.py
============
4銘柄の基準価額を取得するモジュール。

取得戦略:
  日経新聞 投資信託情報ページ（https://www.nikkei.com/nkd/fund/）から取得する。
  失敗時はリトライ（settings.json の retry_count / retry_interval_sec に従う）

戻り値: fetch_all_nav_quotes() は { 資産ID: {"value": 基準価額, "as_of": 基準日(date|None)} | None }。
fetch_all_nav() は値だけの { 資産ID: float | None } を返す。取得失敗したファンドは None になる
基準日は、ページの「基準価格(11/24)： 42,418 円」の日付（freshness.parse_nikkei_asof）
"""

import json
import time
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from freshness import parse_nikkei_asof

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 設定ファイルのパス
SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"

# 共通リクエストヘッダー（ブラウザに偽装）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 15  # 秒
JST = timezone(timedelta(hours=9))

def load_settings() -> dict:
    """settings.json を読み込む"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _extract_nav(soup, today: date) -> tuple[float, date | None] | None:
    """ページ（BeautifulSoup）から (基準価額, 基準日) を取り出す。価格要素がなければ None。基準日が読めなければ None を返す。"""
    el = soup.select_one(".m-stockPriceElm_value")
    if not el:
        return None
    value = float(el.text.replace(",", "").replace("円", "").strip())
    as_of = None
    for parent in el.parents:   # 価格要素を含む最小の塊（dl）の文字列から基準日を探す
        as_of = parse_nikkei_asof(parent.get_text(" ", strip=True), today)
        if as_of is not None or parent.name in ("body", "html"):
            break
    return value, as_of


def _fetch_nikkei_nav(fund_code: str, short_name: str) -> dict | None:
    """
    日経新聞の投資信託ページから基準価額と基準日を取得する。
    Returns: {"value": float, "as_of": date | None}。取得失敗は None
    """
    url = f"https://www.nikkei.com/nkd/fund/?fcode={fund_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        extracted = _extract_nav(soup, datetime.now(JST).date())
        if extracted is None:
            logger.warning(f"{short_name}: 価格要素 (.m-stockPriceElm_value) が見つかりませんでした")
            return None
        value, as_of = extracted
        logger.info(f"{short_name}: {value:,.0f}円 (日経・基準日 {as_of or '不明'})")
        return {"value": value, "as_of": as_of}
    except Exception as e:
        logger.warning(f"{short_name} 取得失敗: {e}")
        return None

def fetch_all_nav_quotes(retry_count: int = 3, retry_interval: int = 30) -> dict:
    """
    全ファンドの基準価額と基準日を取得する。取得失敗したファンドは None になる。

    Returns:
        {資産ID: {"value": float, "as_of": date | None} | None}
    """
    settings = load_settings()
    results = {}

    for fund in settings["funds"]:
        fid = fund["id"]
        fund_code = fund["fund_code"]
        short_name = fund["short_name"]
        
        quote = None
        for attempt in range(1, retry_count + 1):
            try:
                quote = _fetch_nikkei_nav(fund_code, short_name)
                if quote is not None:
                    break
            except Exception as e:
                logger.warning(f"{short_name} 試行 {attempt}/{retry_count} 例外: {e}")

            if attempt < retry_count:
                logger.info(f"{short_name} {retry_interval}秒後にリトライします...")
                time.sleep(retry_interval)

        if quote is None:
            logger.error(f"{short_name}: 全{retry_count}回の取得に失敗しました")
        results[fid] = quote

    return results


def fetch_all_nav(retry_count: int = 3, retry_interval: int = 30) -> dict:
    """全ファンドの基準価額だけを返す（{資産ID: float | None}）。基準日が必要な場合は fetch_all_nav_quotes()。"""
    return {fid: (q["value"] if q else None) for fid, q in fetch_all_nav_quotes(retry_count, retry_interval).items()}

if __name__ == "__main__":
    settings = load_settings()
    retry = settings["notification"]["retry_count"]
    interval = settings["notification"]["retry_interval_sec"]

    logger.info("=== 基準価額取得テスト ===")
    quotes = fetch_all_nav_quotes(retry_count=1, retry_interval=1)

    print("\n【取得結果】")
    for fund_id, q in quotes.items():
        if q is not None:
            print(f"  {fund_id:14s}: {q['value']:>10,.0f} 円  基準日 {q['as_of'] or '不明'}")
        else:
            print(f"  {fund_id:14s}: 取得失敗")
