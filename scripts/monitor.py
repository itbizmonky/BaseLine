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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 日本標準時（UTC+9固定オフセット。tzdata依存を避けるため固定オフセットを使用）
JST = timezone(timedelta(hours=9))

# スクリプトディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from assets import KIND_FUND, all_assets, stock_assets
from fetch_nav import fetch_all_nav_quotes, load_settings
from freshness import DEFAULT_MAX_LAG_WEEKDAYS, STATUS_STALE, classify_prices, prev_weekday, short_date, to_date, usable_values
from market_data import (
    fetch_all_market_data, load_market, save_market, update_market,
    judge_vix_level, calc_direction,
)
from positions import (
    fetch_all_position_quotes, load_positions, save_positions, update_positions,
    load_positions_history, append_positions_history,
)
from purchase_history import calc_gain_vs_average, cost_records, load_purchase_history, purchase_markers
from judge import (
    load_peak, save_peak,
    load_triggered, save_triggered,
    load_history, append_history,
    update_peak,
    calc_drawdown, judge_tier, calc_day_change,
    update_alert_state, calc_trend,
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


def fetch_stocks(settings: dict, today_str: str, usdjpy: float | None, max_lag: int = DEFAULT_MAX_LAG_WEEKDAYS) -> tuple[dict, dict]:
    """
    米国個別株の株価と取引日を取得し、(判定に使える株価 {資産ID: USD}, 表示用 quotes) を返す。
    取得に失敗した株は前回値（forced_stale）、取引日が古い株は stale として quotes に入れ、判定に使える株価
    （最高値更新・Tier判定・履歴追記の入力）には含めない。失敗しても処理は止めない。
    quotes: {資産ID: {"value": float, "date": 基準日(YYYY-MM-DD), "stale": bool, "status": "fresh"|"stale"|"unknown"}}
    """
    usable, quotes = {}, {}
    stocks = stock_assets(settings)
    if not stocks:
        return usable, quotes
    try:
        today = date.fromisoformat(today_str)
        old_positions = load_positions()
        fetched_q = fetch_all_position_quotes(settings)
        incoming = {}
        for s in stocks:
            sid = s["id"]
            q = fetched_q.get(sid)
            if q is not None:
                incoming[sid] = {"value": q["value"], "as_of": q["as_of"]}
            elif sid in old_positions:
                incoming[sid] = {"value": old_positions[sid]["value"], "as_of": old_positions[sid]["date"], "forced_stale": True}
        info = classify_prices(incoming, today, max_lag)
        usable = usable_values(info)
        for sid, i in info.items():
            quotes[sid] = {
                "value": i["value"],
                "date": (i["as_of"] or prev_weekday(today)).isoformat(),   # 取引日が不明なら直近の平日で代用
                "stale": i["status"] == STATUS_STALE,
                "status": i["status"],
            }
        fetched = {sid: q["value"] for sid, q in fetched_q.items() if q is not None}
        save_positions(update_positions(old_positions, fetched, today_str, {sid: q["as_of"] for sid, q in fetched_q.items() if q}))
        by_as_of = {}
        for sid, value in usable.items():
            by_as_of.setdefault(quotes[sid]["date"], {})[sid] = value
        append_positions_history(by_as_of, usdjpy, today_str)   # 取引日をキーに記録（休場日の再実行では行が増えない）
        logger.info(f"米国株: {fetched}（判定に使える: {usable}）")
    except Exception as e:
        # 米国株の取得失敗で投資信託のTier判定・通知が止まらないよう、例外を握りつぶす
        logger.warning(f"米国株の取得中にエラーが発生しましたが処理を続行します: {e}")
    return usable, quotes


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
    max_lag = settings.get("freshness", {}).get("max_lag_weekdays", DEFAULT_MAX_LAG_WEEKDAYS)

    # ----------------------------------------------------------
    # 2. 基準価額取得（投資信託）・市場心理・米国株（参考情報・米国株は取得失敗しても止めない）
    # ----------------------------------------------------------
    logger.info("基準価額を取得中...")
    nav_quotes = fetch_all_nav_quotes(
        retry_count=retry_count,
        retry_interval=retry_interval if not dry_run else 1,
    )
    navs = {fid: (q["value"] if q else None) for fid, q in nav_quotes.items()}
    # 基準日が古い（stale）NAVは、最高値更新・Tier判定・履歴追記に使わない
    nav_info = classify_prices(nav_quotes, today, max_lag)
    usable_navs = usable_values(nav_info)
    fallback_day = prev_weekday(today)   # 基準日が取れない場合の代用（直近の平日）
    nav_as_of = {fid: (i["as_of"] or fallback_day).isoformat() for fid, i in nav_info.items() if i}
    for fid, i in nav_info.items():
        if i and i["status"] == "unknown":
            logger.warning(f"{fid}: 基準日を取得できませんでした（価格は使用します）")
    market_display = fetch_market(settings, today_str)
    fresh_stocks, quotes = fetch_stocks(settings, today_str, market_display.get("usdjpy", {}).get("value"), max_lag)

    # ----------------------------------------------------------
    # 3. 取得失敗チェック
    # ----------------------------------------------------------
    names = {a["id"]: a["short_name"] for a in assets}
    failed_ids = [fid for fid, nav in navs.items() if nav is None]
    failed_ids += [s["id"] for s in stock_assets(settings) if s["id"] not in quotes]
    stale_dates = {fid: i["as_of"] for fid, i in nav_info.items() if i and i["status"] == STATUS_STALE}
    stale_dates.update({sid: to_date(q["date"]) for sid, q in quotes.items() if q["stale"]})
    failed_names = [names.get(fid, fid) for fid in failed_ids]
    failed_names += [f"{names.get(fid, fid)}（基準日{short_date(d)}・判定保留）" for fid, d in stale_dates.items()]
    if failed_names:
        logger.warning(f"取得失敗・基準日が古い: {failed_names}")
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
    price_as_of = {**nav_as_of, **{sid: quotes[sid]["date"] for sid in fresh_stocks}}   # 価格の基準日（最高値の日付に使う）
    peak, _ = update_peak(peak, {**usable_navs, **fresh_stocks}, today_str, peak_start,
                          history + positions_history, baseline, extra_points, price_as_of)

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
            info = nav_info.get(aid)
            price_date = nav_as_of.get(aid, today_str)
            stale = bool(info and info["status"] == STATUS_STALE)
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
            "day_change": None if stale else calc_day_change(trend_rows, aid, price, price_date),
            "drawdown": None,
            "tier": 0,
            "avg_cost_ratio": None,
            "trend_5d": calc_trend(trend_rows, aid, days=5),
            "trend_20d": calc_trend(trend_rows, aid, days=20),
        }
        if price is None or peak_val is None or stale:
            # 取得失敗・基準日が古い価格は、下落率・Tier判定・通知状態の更新をしない（判定保留）
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

        # alert対象は、新しいTierに到達したときだけLINE通知する。Tierごとに独立して武装・通知済みを持ち
        # （重複通知防止・回復後に再武装）、同時に複数越えても最も深いTierを1通だけ通知する。
        # 通知しても購入額・BUY表示・原資の消費は伴わない
        if asset["alert"]:
            notify_tier = update_alert_state(aid, drawdown, asset["tiers"], triggered, rearm_ratio, today_str)
            if notify_tier:
                if not dry_run and notifications_enabled:
                    notify_tier_reached(asset, notify_tier, drawdown, avg_ratio, dashboard_url)
                else:
                    logger.info(f"[DRY RUN or 通知OFF] {asset['short_name']} Tier{notify_tier} 到達通知をスキップ")

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

    nav_rows = {}
    for fid, value in usable_navs.items():
        nav_rows.setdefault(nav_as_of[fid], {})[fid] = value
    append_history(nav_rows, today_str)   # 基準日をキーに記録（祝日・未更新の日の再実行では行が増えない）

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
