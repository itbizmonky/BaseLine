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
) -> str:
    """
    Tier到達時の通知メッセージを生成する（要件 F-10）。
    銘柄名 / 到達Tier / 下落率 / 投入予定額 / 実行期間（②or③）を含む。
    """
    tier_emoji = {1: "🟡", 2: "🟠", 3: "🔴"}.get(tier, "⚠️")
    amount = fund_remaining.get("tier_detail", {}).get(f"tier{tier}", {}).get("amount", 0)
    amount_str = f"{amount:,}円" if amount > 0 else "（金額未設定）"

    msg = (
        f"{tier_emoji} 【暴落アラート】 Tier{tier} 到達！\n"
        f"\n"
        f"📌 銘柄: {fund_name}\n"
        f"📉 下落率: ▲{drawdown:.2f}%\n"
        f"   現在値: {current_nav:,.0f}円\n"
        f"   設定来高値: {peak_nav:,.0f}円\n"
        f"\n"
        f"💰 投入予定額（Tier{tier}）: {amount_str}\n"
        f"📅 現在期間: {period_info.get('label', '-')}\n"
        f"   期限まで残り: {period_info.get('days_remaining', '-')}日\n"
        f"\n"
        f"⚠️ 投資判断は必ずご自身でご確認ください。"
    )
    return msg


def build_error_message(failed_funds: list[str], today_str: str) -> str:
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
        f"ダッシュボードで手動確認をお願いします。"
    )


def build_daily_summary_message(
    today_str: str,
    period_info: dict,
    fund_results: list[dict],
) -> str:
    """
    Tier到達がない平常時のデイリーサマリー通知（省略可能・コメントアウト済み）
    要件には明記されていないため、Tier到達時のみ通知する設計。
    この関数は将来の拡張用として定義しておく。
    """
    lines = [f"📊 【日次監視完了】{today_str}"]
    lines.append(f"現在期間: {period_info.get('label', '-')} / 残{period_info.get('days_remaining', '-')}日\n")
    for r in fund_results:
        icon = "🟢" if r["tier"] == 0 else {1: "🟡", 2: "🟠", 3: "🔴"}[r["tier"]]
        lines.append(f"{icon} {r['short_name']}: ▲{r['drawdown']:.2f}% (Tier{r['tier']})")
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
    )
    logger.info(f"Tier到達通知を送信: {fund['short_name']} Tier{tier}")
    return _send_line_message(msg)


def notify_fetch_error(failed_funds: list[str], today_str: str) -> bool:
    """データ取得エラー通知を送信する（要件 F-11）"""
    msg = build_error_message(failed_funds, today_str)
    logger.info(f"エラー通知を送信: {failed_funds}")
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
