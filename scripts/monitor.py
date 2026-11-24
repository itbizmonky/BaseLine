"""
monitor.py
==========
暴落監視ダッシュボード メインエントリポイント。

GitHub Actions から毎日実行されるスクリプト。
手動実行の場合は --dry-run オプションで通知をスキップできる。

実行フロー:
  1. 設定ファイル読み込み
  2. 基準価額取得（fetch_nav.py。投資信託）・米国株の株価取得（positions.py）
  3. 取得失敗チェック → 失敗があればLINE通知
  4. 設定来高値の更新（judge.py。資産ごとの起点日以降）
  5. 全保有資産の下落率（最高値比）・Tier・平均取得単価比の計算
  6. alert 対象の資産で新しいTierに到達したらLINE通知（購入額・BUY表示は出さない「下落の気づき」）
  7. 日次サマリー通知（全保有資産の最高値比・平均取得単価比）
  8. 履歴CSV・高値JSON・通知済みTier保存
  9. HTMLダッシュボード生成（generate_dashboard.py）
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 日本標準時（UTC+9固定オフセット。tzdata依存を避けるため固定オフセットを使用）
JST = timezone(timedelta(hours=9))

# スクリプトディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from assets import KIND_FUND, all_assets, stock_assets
from fetch_nav import fetch_all_nav, load_settings
from market_data import (
    fetch_all_market_data, load_market, save_market, update_market,
    judge_vix_level, calc_direction,
)
from positions import (
    fetch_all_positions, load_positions, save_positions, update_positions,
    load_positions_history, append_positions_history,
)
from purchase_history import calc_gain_vs_average, cost_records, load_purchase_history, purchase_markers
from judge import (
    load_peak, save_peak,
    load_triggered, save_triggered,
    load_history, append_history,
    update_peak,
    calc_drawdown, judge_tier, calc_day_change,
    is_new_trigger, record_notified, rearm_if_recovered, calc_trend,
    DEFAULT_REARM_RATIO,
)
from notify import notify_tier_reached, notify_fetch_error, notify_daily_summary
from generate_dashboard import generate
from groups import build_groups

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def sync_github_workflow(settings: dict) -> None:
    """settings.json の schedule に基づいて monitor.yml の cron を同期する"""
    schedule_cfg = settings.get("schedule", {})
    hour_jst = schedule_cfg.get("hour_jst", 7)
    minute_jst = schedule_cfg.get("minute_jst", 0)
    daily = schedule_cfg.get("daily", True)

    # JST(日本時間)をUTC(協定世界時)に変換
    # 日本時間 7:00 は UTC 22:00 (前日)
    hour_utc = (hour_jst - 9) % 24
    minute_utc = minute_jst

    if daily:
        day_of_week = "*"
    else:
        # JST平日(月〜金)がUTC側でどの曜日になるかを計算する。
        # hour_jst < 9 の場合、UTC側の日付は前日にずれるため、
        # 単純に "1-5" を使うとJSTの平日と一致しない（cron曜日: 0=日〜6=土）。
        day_shift = -1 if hour_jst < 9 else 0
        jst_weekdays = [1, 2, 3, 4, 5]  # 月〜金
        utc_weekdays = sorted((d + day_shift) % 7 for d in jst_weekdays)
        if utc_weekdays == list(range(utc_weekdays[0], utc_weekdays[0] + len(utc_weekdays))):
            day_of_week = f"{utc_weekdays[0]}-{utc_weekdays[-1]}"
        else:
            day_of_week = ",".join(str(d) for d in utc_weekdays)

    cron_utc = f"{minute_utc} {hour_utc} * * {day_of_week}"

    workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "monitor.yml"
    if not workflow_path.exists():
        logger.warning(f"Workflow file not found at {workflow_path}")
        return

    try:
        content = workflow_path.read_text(encoding="utf-8")
        import re
        pattern = r"- cron:\s*['\"].*?['\"]"
        replacement = f"- cron: '{cron_utc}'"

        new_content, count = re.subn(pattern, replacement, content)
        if count > 0 and new_content != content:
            workflow_path.write_text(new_content, encoding="utf-8")
            logger.info(
                f"Updated GitHub Actions workflow cron schedule to JST {hour_jst:02d}:{minute_jst:02d} "
                f"(UTC cron: '{cron_utc}')"
            )
    except Exception as e:
        logger.error(f"Failed to sync workflow file: {e}")


def fetch_market(settings: dict, today_str: str) -> dict:
    """市場心理指標（参考情報）を取得し、ダッシュボード表示用の辞書を返す。失敗しても処理は止めない。"""
    market_display = {}
    try:
        market_settings = settings.get("market_indicators", {})
        if market_settings.get("enabled", True):
            old_market = load_market()
            fetched_market = fetch_all_market_data(settings)
            vix_thresholds = market_settings.get("vix_thresholds")

            if fetched_market.get("vix") is not None:
                market_display["vix"] = {
                    "value": fetched_market["vix"],
                    "date": today_str,
                    "level": judge_vix_level(fetched_market["vix"], vix_thresholds),
                }
            elif "vix" in old_market:
                market_display["vix"] = {
                    **old_market["vix"],
                    "level": judge_vix_level(old_market["vix"]["value"], vix_thresholds),
                }

            for key in ("us10y", "usdjpy"):
                prev_value = old_market.get(key, {}).get("value")
                cur_value = fetched_market.get(key)
                if cur_value is not None:
                    market_display[key] = {
                        "value": cur_value,
                        "date": today_str,
                        "direction": calc_direction(cur_value, prev_value),
                    }
                elif key in old_market:
                    market_display[key] = {
                        **old_market[key],
                        "direction": {"diff": None, "arrow": "→"},
                    }

            new_market = update_market(old_market, fetched_market, today_str)
            save_market(new_market)
            logger.info(f"市場心理指標: {fetched_market}")
    except Exception as e:
        # 参考情報の取得失敗でTier判定・通知が止まらないよう、例外を握りつぶす
        logger.warning(f"市場心理指標の取得中にエラーが発生しましたが処理を続行します: {e}")
    return market_display


def fetch_stocks(settings: dict, today_str: str, usdjpy: float | None) -> tuple[dict, dict]:
    """
    米国個別株の株価を取得し、(取得できた株価 {資産ID: USD}, 表示用 quotes) を返す。
    取得に失敗した株は前回値（stale=True）を quotes に入れる。失敗しても処理は止めない。
    quotes: {資産ID: {"value": float, "date": str, "stale": bool}}
    """
    fresh, quotes = {}, {}
    stocks = stock_assets(settings)
    if not stocks:
        return fresh, quotes
    try:
        old_positions = load_positions()
        fetched = fetch_all_positions(settings)
        for s in stocks:
            sid = s["id"]
            if fetched.get(sid) is not None:
                fresh[sid] = fetched[sid]
                quotes[sid] = {"value": fetched[sid], "date": today_str, "stale": False}
            elif sid in old_positions:
                quotes[sid] = {"value": old_positions[sid]["value"], "date": old_positions[sid]["date"], "stale": True}
        save_positions(update_positions(old_positions, fetched, today_str))
        append_positions_history(today_str, {**fetched, "usdjpy": usdjpy}, [s["id"] for s in stocks] + ["usdjpy"])
        logger.info(f"米国株: {fetched}")
    except Exception as e:
        # 米国株の取得失敗で投資信託のTier判定・通知が止まらないよう、例外を握りつぶす
        logger.warning(f"米国株の取得中にエラーが発生しましたが処理を続行します: {e}")
    return fresh, quotes


def main(dry_run: bool = False) -> None:
    """
    メイン実行関数。

    Args:
        dry_run: True の場合、LINE通知をスキップする（テスト用）
    """
    today = datetime.now(JST).date()
    today_str = today.strftime("%Y-%m-%d")
    logger.info(f"=== 暴落監視 開始: {today_str} (JST) {'[DRY RUN]' if dry_run else ''} ===")

    # 土日は投信の基準価額が更新されないため、本番実行時はスキップする
    # （--dry-run はダッシュボード確認用に平日以外でも実行できるようにする）
    if not dry_run and today.weekday() >= 5:
        logger.info(f"{today_str} はJST基準で土日のため、監視処理をスキップします。")
        return

    # ----------------------------------------------------------
    # 1. 設定読み込み
    # ----------------------------------------------------------
    settings = load_settings()
    sync_github_workflow(settings)

    assets = all_assets(settings)
    dashboard_url = settings.get("dashboard_url", "https://itbizmonky.github.io/BaseLine/")
    notifications_enabled = settings.get("notification", {}).get("enabled", True)
    retry_count = settings["notification"]["retry_count"]
    retry_interval = settings["notification"]["retry_interval_sec"]
    rearm_ratio = settings.get("alert", {}).get("rearm_ratio", DEFAULT_REARM_RATIO)

    # ----------------------------------------------------------
    # 2. 基準価額取得（投資信託）・市場心理・米国株（参考情報・米国株は取得失敗しても止めない）
    # ----------------------------------------------------------
    logger.info("基準価額を取得中...")
    navs = fetch_all_nav(
        retry_count=retry_count,
        retry_interval=retry_interval if not dry_run else 1,
    )
    market_display = fetch_market(settings, today_str)
    fresh_stocks, quotes = fetch_stocks(settings, today_str, market_display.get("usdjpy", {}).get("value"))

    # ----------------------------------------------------------
    # 3. 取得失敗チェック
    # ----------------------------------------------------------
    failed_ids = [fid for fid, nav in navs.items() if nav is None]
    failed_ids += [s["id"] for s in stock_assets(settings) if s["id"] not in fresh_stocks]
    if failed_ids:
        names = {a["id"]: a["short_name"] for a in assets}
        failed_names = [names.get(fid, fid) for fid in failed_ids]
        logger.warning(f"取得失敗: {failed_names}")
        if not dry_run and notifications_enabled:
            notify_fetch_error(failed_names, today_str, dashboard_url)

    # 全ファンド失敗の場合は終了
    if all(v is None for v in navs.values()):
        logger.error("全銘柄の取得に失敗しました。処理を終了します。")
        sys.exit(1)

    # ----------------------------------------------------------
    # 4. 設定来高値の更新（資産ごとの起点日以降。履歴に無い期間は約定単価も参考にする）
    # ----------------------------------------------------------
    peak = load_peak()
    peak_start = settings.get("peak_start_date", "2026-08-01")
    history = load_history()
    positions_history = load_positions_history()
    baseline = settings.get("baseline", {})
    purchases = load_purchase_history()
    extra_points = {
        aid: [(m["date"], m["price"]) for m in purchase_markers(records)]
        for aid, records in purchases.items()
    }
    peak, _ = update_peak(peak, {**navs, **fresh_stocks}, today_str, peak_start,
                          history + positions_history, baseline, extra_points)

    # ----------------------------------------------------------
    # 5. 全保有資産の下落率・Tier・平均取得単価比・（alert対象のみ）通知
    # ----------------------------------------------------------
    alert_ids = {a["id"] for a in assets if a["alert"]}
    triggered = {k: v for k, v in load_triggered().items() if k in alert_ids}
    results = []

    for asset in assets:
        aid = asset["id"]
        if asset["kind"] == KIND_FUND:
            price = navs.get(aid)
            price_date, stale = today_str, False
            trend_rows = history
        else:
            quote = quotes.get(aid) or {}
            price = quote.get("value")
            price_date, stale = quote.get("date", today_str), bool(quote.get("stale"))
            trend_rows = positions_history

        peak_info = peak.get(aid, {})
        peak_val = peak_info.get("value")

        result = {
            "asset_id": aid,
            "name": asset["name"],
            "short_name": asset["short_name"],
            "kind": asset["kind"],
            "currency": asset["currency"],
            "role": asset["role"],
            "color": asset["color"],
            "alert": asset["alert"],
            "thresholds": asset["tiers"],
            "price": price,
            "price_date": price_date,
            "stale": stale,
            "peak_value": peak_val,
            "peak_date": peak_info.get("date"),
            "day_change": calc_day_change(trend_rows, aid, price, today_str),
            "drawdown": None,
            "tier": 0,
            "avg_cost_ratio": None,
            "trend_5d": calc_trend(trend_rows, aid, days=5),
            "trend_20d": calc_trend(trend_rows, aid, days=20),
        }
        if price is None or peak_val is None:
            results.append(result)
            continue

        drawdown = calc_drawdown(price, peak_val)
        tier = judge_tier(drawdown, asset["tiers"])
        avg_ratio = calc_gain_vs_average(cost_records(purchases.get(aid, [])), price)
        result.update({
            "drawdown": drawdown,
            "tier": tier,
            "avg_cost_ratio": avg_ratio,
        })
        results.append(result)
        logger.info(
            f"{asset['short_name']}: 現在値={price:,.2f} / 高値={peak_val:,.2f} / 最高値比={drawdown:.2f}% / "
            f"前日比={result['day_change'] if result['day_change'] is None else format(result['day_change'], '+.2f') + '%'} / Tier={tier}"
        )

        # alert対象は、新しいTierに到達したときだけLINE通知する（重複通知防止・回復後に再武装）。
        # 通知しても購入額・BUY表示・原資の消費は伴わない
        if asset["alert"]:
            triggered = rearm_if_recovered(aid, drawdown, asset["tiers"], triggered, rearm_ratio)
            if is_new_trigger(aid, tier, triggered):
                if not dry_run and notifications_enabled:
                    notify_tier_reached(asset, tier, drawdown, avg_ratio, dashboard_url)
                else:
                    logger.info(f"[DRY RUN or 通知OFF] {asset['short_name']} Tier{tier} 到達通知をスキップ")
                triggered = record_notified(aid, tier, triggered, date=today_str)

    # ----------------------------------------------------------
    # 6. 日次サマリー通知（全保有資産の最高値比・平均取得単価比）
    # ----------------------------------------------------------
    groups = build_groups(settings, purchases, results, market_display.get("usdjpy", {}).get("value"), today_str)
    if not dry_run and notifications_enabled:
        notify_daily_summary(today_str, results, dashboard_url, groups)
    else:
        logger.info("[DRY RUN or 通知OFF] デイリーサマリー通知をスキップ")

    # ----------------------------------------------------------
    # 7. データ保存
    # ----------------------------------------------------------
    save_peak(peak)
    logger.info("peak.json を保存しました")

    save_triggered(triggered)
    logger.info("triggered.json を保存しました")

    append_history(today_str, navs)

    # ----------------------------------------------------------
    # 8. HTMLダッシュボード生成
    # ----------------------------------------------------------
    logger.info("ダッシュボードHTMLを生成中...")
    generate(
        today_str=today_str,
        results=results,
        peak=peak,
        history=load_history(),  # 追記後の最新データを再読み込み
        positions_history=load_positions_history(),
        settings=settings,
        triggered=triggered,
        market_display=market_display,
        quotes=quotes,
        purchase_history=purchases,
    )

    logger.info(f"=== 暴落監視 完了: {today_str} ===")


# ------------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="暴落監視ダッシュボード 実行スクリプト")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="LINE通知をスキップしてダッシュボード生成のみ実行する（テスト用）",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
