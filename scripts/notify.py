"""
notify.py
=========
LINE Messaging API を使った Push通知モジュール。

環境変数から以下を読み込む:
  LINE_CHANNEL_ACCESS_TOKEN  : チャンネルアクセストークン（長期）
  LINE_USER_ID               : 通知先ユーザーID（自分自身の "U" から始まるID）

GitHub Secrets に設定して使う。
ローカルテスト時は .env ファイルまたは環境変数を手動でセットする。
"""

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

LINE_API_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _get_credentials() -> tuple[str | None, str | None]:
    """環境変数からLINE APIの認証情報を取得する"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    return token, user_id


def _send_line_message(text: str) -> bool:
    """
    LINE Messaging API でプッシュメッセージを送信する。

    Returns:
        True: 送信成功 / False: 送信失敗
    """
    token, user_id = _get_credentials()

    if not token or not user_id:
        logger.error(
            "LINE_CHANNEL_ACCESS_TOKEN または LINE_USER_ID が設定されていません。"
            "GitHub Secrets（またはローカル環境変数）を確認してください。"
        )
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }

    try:
        resp = requests.post(LINE_API_PUSH_URL, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info("LINE通知送信成功")
            return True
        else:
            logger.error(f"LINE通知失敗: status={resp.status_code}, body={resp.text}")
            return False
    except Exception as e:
        logger.error(f"LINE通知例外: {e}")
        return False


# ------------------------------------------------------------------
# 通知メッセージ生成
# ------------------------------------------------------------------

def build_tier_message(
    fund_name: str,
    short_name: str,
    tier: int,
    drawdown: float,
    current_nav: float,
    peak_nav: float,
    period_info: dict,
    fund_remaining: dict,
    decision: str,
    baseline_ratio: float,
    dashboard_url: str,
) -> str:
    """
    Tier到達時の通知メッセージを生成する（要件 F-10）。
    """
    decision_emoji = {"BUY": "🟢 BUY", "WAIT": "🟡 WAIT", "HOLD": "⚪ HOLD", "HIGH": "🔴 HIGH"}.get(decision, decision)
    amount = fund_remaining.get("tier_detail", {}).get(f"tier{tier}", {}).get("amount", 0)
    amount_str = f"{amount:,}円" if amount > 0 else "（金額未設定）"

    days_rem = period_info.get('days_remaining', '-')
    if period_info.get('phase') == 'before_start':
        period_days_str = f"   開始まであと: {days_rem}日"
    elif period_info.get('phase') == 'ended':
        period_days_str = "   監視期間終了"
    else:
        period_days_str = f"   期限まで残り: {days_rem}日"

    msg = (
        f"🚨 【暴落アラート】 {decision_emoji} {short_name} Tier{tier} 到達！\n"
        f"\n"
        f"📌 銘柄: {fund_name}\n"
        f"📉 最高値比: -{drawdown:.2f}%\n"
        f"📈 基準日比: {baseline_ratio:+.2f}%\n"
        f"   現在値: {current_nav:,.0f}円\n"
        f"   設定来高値: {peak_nav:,.0f}円\n"
        f"\n"
        f"💰 投入予定額（Tier{tier}）: {amount_str}\n"
        f"📅 現在期間: {period_info.get('label', '-')}\n"
        f"{period_days_str}\n"
        f"\n"
        f"💻 詳細ダッシュボード:\n{dashboard_url}\n"
        f"\n"
        f"⚠️ 投資判断は必ずご自身でご確認ください。"
    )
    return msg


def build_error_message(failed_funds: list[str], today_str: str, dashboard_url: str) -> str:
    """
    データ取得失敗時のエラー通知メッセージを生成する（要件 F-11）。
    """
    fund_list = "、".join(failed_funds)
    return (
        f"⚠️ 【監視エラー】データ取得失敗\n"
        f"\n"
        f"📅 日付: {today_str}\n"
        f"❌ 取得失敗銘柄: {fund_list}\n"
        f"\n"
        f"本日の監視が完了していない可能性があります。\n"
        f"詳細ダッシュボードで手動確認をお願いします。\n"
        f"{dashboard_url}"
    )


def build_daily_summary_message(
    today_str: str,
    period_info: dict,
    fund_results: list[dict],
    dashboard_url: str,
) -> str:
    """
    日次の監視サマリー通知
    """
    lines = [f"📊 【日次監視完了】{today_str}\n"]
    for r in fund_results:
        d = r.get("decision", "HOLD")
        dec_emoji = {"BUY": "🟢 BUY", "WAIT": "🟡 WAIT", "HOLD": "⚪ HOLD", "HIGH": "🔴 HIGH"}.get(d, d)
        tier_val = r.get("tier", 0)
        tier_str = f"Tier{tier_val}" if tier_val > 0 else "Tier未到達"
        lines.append(f"{dec_emoji} {r['short_name']}:\n   最高値比: -{r['drawdown']:.1f}% / 基準日比: {r['baseline_ratio']:+.1f}% ({tier_str})")
    
    phase_type = period_info.get('phase', 'none')
    days_rem = period_info.get('days_remaining', '-')
    if phase_type == 'before_start':
        period_str = f"{period_info.get('label', '-')} (開始まであと{days_rem}日)"
    elif phase_type == 'ended':
        period_str = f"{period_info.get('label', '-')}"
    else:
        period_str = f"{period_info.get('label', '-')} (残{days_rem}日)"
        
    lines.append(f"\n📅 現在期間: {period_str}")
    lines.append(f"💻 詳細ダッシュボード:\n{dashboard_url}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# 通知実行関数
# ------------------------------------------------------------------

def notify_tier_reached(
    fund: dict,
    tier: int,
    drawdown: float,
    current_nav: float,
    peak_nav: float,
    period_info: dict,
    fund_remaining: dict,
    decision: str,
    baseline_ratio: float,
    dashboard_url: str,
) -> bool:
    """Tier到達通知を送信する（要件 F-09）"""
    msg = build_tier_message(
        fund_name=fund["name"],
        short_name=fund["short_name"],
        tier=tier,
        drawdown=drawdown,
        current_nav=current_nav,
        peak_nav=peak_nav,
        period_info=period_info,
        fund_remaining=fund_remaining,
        decision=decision,
        baseline_ratio=baseline_ratio,
        dashboard_url=dashboard_url,
    )
    logger.info(f"Tier到達通知を送信: {fund['short_name']} Tier{tier}")
    return _send_line_message(msg)


def notify_fetch_error(failed_funds: list[str], today_str: str, dashboard_url: str) -> bool:
    """データ取得エラー通知を送信する（要件 F-11）"""
    msg = build_error_message(failed_funds, today_str, dashboard_url)
    logger.info(f"エラー通知を送信: {failed_funds}")
    return _send_line_message(msg)


def notify_daily_summary(today_str: str, period_info: dict, fund_results: list[dict], dashboard_url: str) -> bool:
    """日次の監視サマリー通知を送信する"""
    msg = build_daily_summary_message(today_str, period_info, fund_results, dashboard_url)
    logger.info("デイリーサマリー通知を送信")
    return _send_line_message(msg)


# ------------------------------------------------------------------
# スタンドアローン実行（テスト送信用）
# ------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="LINE通知テスト送信")
    parser.add_argument("--test", action="store_true", help="テストメッセージを送信する")
    args = parser.parse_args()

    if args.test:
        print("LINE通知テスト送信を実行します...")
        print("環境変数:")
        token, user_id = _get_credentials()
        print(f"  LINE_CHANNEL_ACCESS_TOKEN: {'設定済み ✓' if token else '未設定 ✗'}")
        print(f"  LINE_USER_ID: {'設定済み ✓' if user_id else '未設定 ✗'}")

        if not token or not user_id:
            print("\n環境変数を設定してから再実行してください:")
            print("  $env:LINE_CHANNEL_ACCESS_TOKEN = 'YOUR_TOKEN'   # PowerShell")
            print("  $env:LINE_USER_ID = 'YOUR_USER_ID'")
            sys.exit(1)

        ok = _send_line_message(
            "✅ 暴落監視ダッシュボード\n\nLINE通知テスト送信に成功しました！\n"
            "本番運用の準備ができています。"
        )
        if ok:
            print("✅ テスト送信成功！LINEを確認してください。")
        else:
            print("❌ テスト送信失敗。ログを確認してください。")
            sys.exit(1)
    else:
        print("使い方: python scripts/notify.py --test")
