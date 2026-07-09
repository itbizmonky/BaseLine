"""
fetch_nav.py
============
4銘柄の基準価額を取得するモジュール。

取得戦略:
  日経新聞 投資信託情報ページ（https://www.nikkei.com/nkd/fund/）から取得する。
  失敗時はリトライ（settings.json の retry_count / retry_interval_sec に従う）

戻り値: dict { "fang": float, "sox": float, "sp500": float, "orkan": float }
取得失敗したファンドは None になる
"""

import json
import time
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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

def load_settings() -> dict:
    """settings.json を読み込む"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _fetch_nikkei_nav(fund_code: str, short_name: str) -> float | None:
    """
    日経新聞の投資信託ページから基準価額を取得する
    """
    url = f"https://www.nikkei.com/nkd/fund/?fcode={fund_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        
        el = soup.select_one(".m-stockPriceElm_value")
        if el:
            text = el.text.replace(",", "").replace("円", "").strip()
            val = float(text)
            logger.info(f"{short_name}: {val:,.0f}円 (日経)")
            return val
        else:
            logger.warning(f"{short_name}: 価格要素 (.m-stockPriceElm_value) が見つかりませんでした")
            return None
    except Exception as e:
        logger.warning(f"{short_name} 取得失敗: {e}")
        return None

def fetch_all_nav(retry_count: int = 3, retry_interval: int = 30) -> dict:
    """
    4銘柄すべての基準価額を取得する。
    取得失敗したファンドは None になる。

    Returns:
        {"fang": float|None, "sox": float|None, "sp500": float|None, "orkan": float|None}
    """
    settings = load_settings()
    results = {}

    for fund in settings["funds"]:
        fid = fund["id"]
        fund_code = fund["fund_code"]
        short_name = fund["short_name"]
        
        nav = None
        for attempt in range(1, retry_count + 1):
            try:
                nav = _fetch_nikkei_nav(fund_code, short_name)
                if nav is not None:
                    break
            except Exception as e:
                logger.warning(f"{short_name} 試行 {attempt}/{retry_count} 例外: {e}")

            if attempt < retry_count:
                logger.info(f"{short_name} {retry_interval}秒後にリトライします...")
                time.sleep(retry_interval)

        if nav is None:
            logger.error(f"{short_name}: 全{retry_count}回の取得に失敗しました")
        results[fid] = nav

    return results

if __name__ == "__main__":
    settings = load_settings()
    retry = settings["notification"]["retry_count"]
    interval = settings["notification"]["retry_interval_sec"]

    logger.info("=== 基準価額取得テスト ===")
    navs = fetch_all_nav(retry_count=1, retry_interval=1)

    print("\n【取得結果】")
    for fund_id, nav in navs.items():
        if nav is not None:
            print(f"  {fund_id:8s}: {nav:>10,.0f} 円")
        else:
            print(f"  {fund_id:8s}: 取得失敗")
