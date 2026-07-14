"""
monitor.py
==========
暴落監視ダッシュボード メインエントリポイント。

GitHub Actions から毎日実行されるスクリプト。
手動実行の場合は --dry-run オプションで通知をスキップできる。

実行フロー:
  1. 設定ファイル読み込み
  2. 基準価額取得（fetch_nav.py）
  3. 取得失敗チェック → 失敗があればLINE通知
  4. 設定来高値の更新（judge.py）
  5. 下落率計算・Tier判定（judge.py）
  6. 新規Tier到達があればLINE通知（notify.py）
  7. 履歴CSV・高値JSON・発動済JSON保存
  8. HTMLダッシュボード生成（generate_dashboard.py）
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 日本標準時（UTC+9固定オフセット。tzdata依存を避けるため固定オフセットを使用）
JST = timezone(timedelta(hours=9))

# スクリプトディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from fetch_nav import fetch_all_nav, load_settings
from judge import (
    load_peak, save_peak,
    load_triggered, save_triggered,
    load_history, append_history,
    update_peak,
    calc_drawdown, judge_tier, calc_baseline_ratio, judge_decision,
    is_new_trigger, record_trigger,
    detect_period, calc_trend, calc_remaining_funds,
)
from notify import notify_tier_reached, notify_fetch_error, notify_daily_summary
from generate_dashboard import generate

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
    
    funds = settings["funds"]
    dashboard_url = settings.get("dashboard_url", "https://itbizmonky.github.io/BaseLine/")
    notifications_enabled = settings.get("notification", {}).get("enabled", True)
    retry_count = settings["notification"]["retry_count"]
    retry_interval = settings["notification"]["retry_interval_sec"]

    # ----------------------------------------------------------
    # 2. 基準価額取得
    # ----------------------------------------------------------
    logger.info("基準価額を取得中...")
    navs = fetch_all_nav(
        retry_count=retry_count,
        retry_interval=retry_interval if not dry_run else 1,
    )

    # ----------------------------------------------------------
    # 3. 取得失敗チェック
    # ----------------------------------------------------------
    failed_funds = [fid for fid, nav in navs.items() if nav is None]
    if failed_funds:
        failed_names = [
            next((f["short_name"] for f in funds if f["id"] == fid), fid)
            for fid in failed_funds
        ]
        logger.warning(f"取得失敗ファンド: {failed_names}")
        if not dry_run and notifications_enabled:
            notify_fetch_error(failed_names, today_str, dashboard_url)

    # 全銘柄失敗の場合は終了
    if all(v is None for v in navs.values()):
        logger.error("全銘柄の取得に失敗しました。処理を終了します。")
        sys.exit(1)

    # ----------------------------------------------------------
    # 4. 設定来高値の更新
    # ----------------------------------------------------------
    peak = load_peak()
    peak_start = settings.get("peak_start_date", "2026-08-01")
    history = load_history()
    baseline = settings.get("baseline", {})
    peak, updated_ids = update_peak(peak, navs, today_str, peak_start, history, baseline)

    # ----------------------------------------------------------
    # 5. 期間判定
    # ----------------------------------------------------------
    period_info = detect_period(today, settings)
    logger.info(f"現在期間: {period_info['label']} / 残{period_info['days_remaining']}日")

    # ----------------------------------------------------------
    # 6. 下落率計算・Tier判定・通知
    # ----------------------------------------------------------
    triggered = load_triggered()
    fund_results = []

    for fund in funds:
        fid = fund["id"]
        nav = navs.get(fid)
        if nav is None:
            fund_results.append({
                "fund_id": fid,
                "short_name": fund["short_name"],
                "tier": 0,
                "drawdown": 0.0,
                "trend_5d": "→",
                "trend_20d": "→",
            })
            continue

        peak_info = peak.get(fid, {})
        peak_val = peak_info.get("value")
        # 監視開始前（peak_start未到達）は高値が記録されないため未計測として扱う。
        # 監視開始後は上記update_peak()で初回シード（history.csv遡り確認込み）済みのため
        # ここで改めて仮登録する必要はない。

        # 下落率・Tier判定（監視開始前で高値未記録の場合は0扱い）
        if peak_val is None:
            drawdown = 0.0
            tier = 0
        else:
            drawdown = calc_drawdown(nav, peak_val)
            tier = judge_tier(drawdown, fund["tiers"])

        # 基準日比・購入判定
        baseline_nav = settings.get("baseline", {}).get("prices", {}).get(fid, 0)
        tolerance_pct = settings.get("baseline", {}).get("initial_price_tolerance_percent", 5.0)
        baseline_ratio = calc_baseline_ratio(nav, baseline_nav)
        is_hwm = (peak_val is not None and drawdown == 0.0)
        decision = judge_decision(tier, nav, baseline_nav, tolerance_pct, is_hwm)

        peak_val_str = f"{peak_val:,.0f}円" if peak_val is not None else "未記録"
        logger.info(
            f"{fund['short_name']}: NAV={nav:,.0f}円 / 高値={peak_val_str} / "
            f"下落率={drawdown:.2f}% / 基準日比={baseline_ratio:+.2f}% / "
            f"Tier={tier} / 判定={decision}"
        )

        # 新規Tier到達 → LINE通知
        if is_new_trigger(fid, tier, triggered):
            phase_key = period_info.get("phase", "phase2")
            if phase_key not in ("phase2", "phase3"):
                phase_key = "phase2"
            remaining = calc_remaining_funds(fid, triggered, phase_key, settings)

            if not dry_run and notifications_enabled:
                notify_tier_reached(
                    fund=fund,
                    tier=tier,
                    drawdown=drawdown,
                    current_nav=nav,
                    peak_nav=peak_val,
                    period_info=period_info,
                    fund_remaining=remaining,
                    decision=decision,
                    baseline_ratio=baseline_ratio,
                    dashboard_url=dashboard_url,
                )
            else:
                logger.info(f"[DRY RUN or 通知OFF] {fund['short_name']} Tier{tier} 到達通知をスキップ")

            triggered = record_trigger(fid, tier, triggered)

        # トレンド計算
        trend_5d = calc_trend(history, fid, days=5)
        trend_20d = calc_trend(history, fid, days=20)

        fund_results.append({
            "fund_id": fid,
            "short_name": fund["short_name"],
            "tier": tier,
            "drawdown": drawdown,
            "trend_5d": trend_5d,
            "trend_20d": trend_20d,
            "baseline_nav": baseline_nav,
            "baseline_ratio": baseline_ratio,
            "decision": decision,
        })

    # ----------------------------------------------------------
    # 6.5 日次サマリー通知
    # ----------------------------------------------------------
    if not dry_run and notifications_enabled:
        notify_daily_summary(today_str, period_info, fund_results, dashboard_url)
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
        navs=navs,
        peak=peak,
        fund_results=fund_results,
        history=load_history(),  # 追記後の最新データを再読み込み
        period_info=period_info,
        settings=settings,
        triggered=triggered,
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
