"""
notify.py
=========
LINE Messaging API を使った Push通知モジュール。

環境変数から以下を読み込む:
  LINE_CHANNEL_ACCESS_TOKEN  : チャンネルアクセストークン（長期）
  LINE_USER_ID               : 通知先ユーザーID（自分自身の "U" から始まるID）

GitHub Secrets に設定して使う。
ローカルテスト時は .env ファイルまたは環境変数を手動でセットする。

通知の内容は「最高比」（最高値比）と「平均比」（平均取得単価比）の2つだけ（購入額・BUY表示・原資・
基準日比などは出さない）。プラスは緑・マイナスは赤で表示するため、通常はFlexメッセージ（カード型）で
送る。Flexの送信に失敗したときは、絵文字（🟢プラス／🔴マイナス）つきのテキストで再送する。
"""

import logging
import os
import sys

import requests

from judge import format_drawdown

logger = logging.getLogger(__name__)

LINE_API_PUSH_URL = "https://api.line.me/v2/bot/message/push"

COLOR_PLUS = "#16a34a"    # 緑（プラス）
COLOR_MINUS = "#dc2626"   # 赤（マイナス）
COLOR_NEUTRAL = "#6b7280"  # グレー（ゼロ・データなし）
COLOR_LABEL = "#9ca3af"

LABEL_DRAWDOWN = "最高比"
LABEL_AVG = "平均比"


def _get_credentials() -> tuple[str | None, str | None]:
    """環境変数からLINE APIの認証情報を取得する"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    return token, user_id


def _push(messages: list[dict]) -> bool:
    """
    LINE Messaging API でプッシュメッセージ（text / flex など）を送信する。

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
    payload = {"to": user_id, "messages": messages}

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


def _send_line_message(text: str) -> bool:
    """テキストメッセージを送信する。"""
    return _push([{"type": "text", "text": text}])


def _send_flex_message(alt_text: str, bubble: dict, fallback_text: str) -> bool:
    """
    Flexメッセージ（色つきのカード）を送信する。送信に失敗したら、同じ内容のテキストで再送する
    （Flexの形式エラーなどで通知が丸ごと失われないようにするため）。
    """
    if _push([{"type": "flex", "altText": alt_text[:400], "contents": bubble}]):
        return True
    logger.warning("Flexメッセージの送信に失敗したため、テキストで再送します")
    return _send_line_message(fallback_text)


# ------------------------------------------------------------------
# 表示の整形
# ------------------------------------------------------------------

def _sign_color(value: float | None) -> str:
    """プラスは緑、マイナスは赤、ゼロ・データなしはグレー。"""
    if value is None or value == 0:
        return COLOR_NEUTRAL
    return COLOR_PLUS if value > 0 else COLOR_MINUS


def _sign_mark(value: float | None) -> str:
    """テキスト版の色の代わりの絵文字（🟢プラス／🔴マイナス／⚪ゼロ・データなし）。"""
    if value is None or value == 0:
        return "⚪"
    return "🟢" if value > 0 else "🔴"


def format_signed_ratio(ratio: float | None, decimals: int = 1) -> str:
    """符号つきの割合（例: +1.5% / -5.4%）。データがなければ「-」。"""
    if ratio is None:
        return "-"
    return f"{ratio:+.{decimals}f}%"


def format_avg_cost_ratio(ratio: float | None) -> str:
    """平均取得単価比を表示用に整形する。購入実績がなければ「-」。"""
    return format_signed_ratio(ratio)


def _drawdown_value(drawdown: float | None, decimals: int = 1) -> float | None:
    """最高比を符号つきの数値にする（下落率は正の値で持つため、マイナスにして返す）。"""
    if drawdown is None:
        return None
    return -drawdown if drawdown > 0 else 0.0


def _drawdown_text(drawdown: float | None, decimals: int = 1) -> str:
    return format_drawdown(drawdown, decimals) if drawdown is not None else "-"


# ------------------------------------------------------------------
# 通知メッセージ生成（テキスト版。Flexの送信に失敗したときの予備を兼ねる）
# ------------------------------------------------------------------

def build_tier_message(
    fund_name: str,
    short_name: str,
    tier: int,
    drawdown: float,
    avg_cost_ratio: float | None,
    dashboard_url: str,
) -> str:
    """
    Tier到達時の「下落の気づき」通知メッセージ（テキスト版）を生成する（要件 F-10）。
    表示するのは最高比と平均比だけ。購入額・判定・期間・原資は出さない。
    """
    return (
        f"🚨 【下落アラート】{short_name} Tier{tier} 到達\n"
        f"\n"
        f"📌 {fund_name}\n"
        f"{_sign_mark(_drawdown_value(drawdown))} {LABEL_DRAWDOWN}: {_drawdown_text(drawdown, 2)}\n"
        f"{_sign_mark(avg_cost_ratio)} {LABEL_AVG}: {format_avg_cost_ratio(avg_cost_ratio)}\n"
        f"\n"
        f"💻 詳細ダッシュボード:\n{dashboard_url}\n"
        f"\n"
        f"⚠️ 投資判断は必ずご自身でご確認ください。"
    )


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
    results: list[dict],
    dashboard_url: str,
) -> str:
    """
    日次の監視サマリー通知（テキスト版）。保有全銘柄を1銘柄1行で「最高比」「平均比」の2値だけ表示する。
    results の各要素: {"short_name", "drawdown", "avg_cost_ratio"}（表示順は渡された順）
    """
    lines = [f"📊 【日次監視完了】{today_str}", f"（{LABEL_DRAWDOWN}／{LABEL_AVG}）", ""]
    for r in results:
        dd = r.get("drawdown")
        avg = r.get("avg_cost_ratio")
        dd_text = f"{_sign_mark(_drawdown_value(dd))}{_drawdown_text(dd)}" if dd is not None else "⚪取得不可"
        lines.append(f"{r['short_name']}\n  {dd_text}　{_sign_mark(avg)}{format_avg_cost_ratio(avg)}")
    lines.append(f"\n💻 詳細ダッシュボード:\n{dashboard_url}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# 通知メッセージ生成（Flex版。プラスは緑・マイナスは赤）
# ------------------------------------------------------------------

def _text(text: str, **kwargs) -> dict:
    return {"type": "text", "text": text, **kwargs}


def _ratio_cell(text: str, value: float | None) -> dict:
    """割合の列。プラス=緑・マイナス=赤・ゼロ/なし=グレー、右寄せ・太字。"""
    return _text(text, size="sm", weight="bold", align="end", color=_sign_color(value), flex=3)


def _link_footer(dashboard_url: str) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "contents": [{"type": "button", "style": "link", "height": "sm",
                      "action": {"type": "uri", "label": "ダッシュボードを開く", "uri": dashboard_url}}],
    }


def build_daily_summary_flex(today_str: str, results: list[dict], dashboard_url: str) -> dict:
    """日次サマリーのFlexメッセージ（bubble）。1銘柄1行で、最高比・平均比を色つきで表示する。"""
    rows = [{
        "type": "box", "layout": "horizontal", "contents": [
            _text("銘柄", size="xs", color=COLOR_LABEL, flex=5),
            _text(LABEL_DRAWDOWN, size="xs", color=COLOR_LABEL, align="end", flex=3),
            _text(LABEL_AVG, size="xs", color=COLOR_LABEL, align="end", flex=3),
        ],
    }, {"type": "separator", "margin": "sm"}]
    for r in results:
        dd = r.get("drawdown")
        avg = r.get("avg_cost_ratio")
        rows.append({
            "type": "box", "layout": "horizontal", "margin": "md", "contents": [
                _text(r["short_name"], size="sm", wrap=True, flex=5),
                _ratio_cell(_drawdown_text(dd) if dd is not None else "-", _drawdown_value(dd)),
                _ratio_cell(format_avg_cost_ratio(avg), avg),
            ],
        })
    return {
        "type": "bubble",
        "size": "mega",
        "header": {"type": "box", "layout": "vertical", "contents": [
            _text("📊 日次監視完了", weight="bold", size="md"),
            _text(today_str, size="xs", color=COLOR_LABEL),
        ]},
        "body": {"type": "box", "layout": "vertical", "contents": rows},
        "footer": _link_footer(dashboard_url),
    }


def build_tier_flex(
    fund_name: str,
    short_name: str,
    tier: int,
    drawdown: float,
    avg_cost_ratio: float | None,
    dashboard_url: str,
) -> dict:
    """Tier到達アラートのFlexメッセージ（bubble）。最高比・平均比だけを色つきで表示する。"""
    def line(label: str, text: str, value: float | None) -> dict:
        return {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
            _text(label, size="sm", color=COLOR_LABEL, flex=2),
            _text(text, size="lg", weight="bold", align="end", color=_sign_color(value), flex=3),
        ]}

    return {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "contents": [
            _text("🚨 下落アラート", weight="bold", size="md", color=COLOR_MINUS),
            _text(f"{short_name}  Tier{tier} 到達", weight="bold", size="lg", wrap=True),
            _text(fund_name, size="xs", color=COLOR_LABEL, wrap=True),
        ]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            line(LABEL_DRAWDOWN, _drawdown_text(drawdown, 2), _drawdown_value(drawdown)),
            line(LABEL_AVG, format_avg_cost_ratio(avg_cost_ratio), avg_cost_ratio),
            _text("投資判断は必ずご自身でご確認ください。", size="xs", color=COLOR_LABEL, margin="lg", wrap=True),
        ]},
        "footer": _link_footer(dashboard_url),
    }


# ------------------------------------------------------------------
# 通知実行関数
# ------------------------------------------------------------------

def notify_tier_reached(
    asset: dict,
    tier: int,
    drawdown: float,
    avg_cost_ratio: float | None,
    dashboard_url: str,
) -> bool:
    """Tier到達通知を送信する（要件 F-09）。asset は settings.json の funds[] の1要素。"""
    bubble = build_tier_flex(asset["name"], asset["short_name"], tier, drawdown, avg_cost_ratio, dashboard_url)
    fallback = build_tier_message(asset["name"], asset["short_name"], tier, drawdown, avg_cost_ratio, dashboard_url)
    alt = f"下落アラート: {asset['short_name']} Tier{tier} 到達（{LABEL_DRAWDOWN}{_drawdown_text(drawdown)}）"
    logger.info(f"Tier到達通知を送信: {asset['short_name']} Tier{tier}")
    return _send_flex_message(alt, bubble, fallback)


def notify_fetch_error(failed_funds: list[str], today_str: str, dashboard_url: str) -> bool:
    """データ取得エラー通知を送信する（要件 F-11）"""
    msg = build_error_message(failed_funds, today_str, dashboard_url)
    logger.info(f"エラー通知を送信: {failed_funds}")
    return _send_line_message(msg)


def notify_daily_summary(today_str: str, results: list[dict], dashboard_url: str) -> bool:
    """日次の監視サマリー通知を送信する"""
    bubble = build_daily_summary_flex(today_str, results, dashboard_url)
    fallback = build_daily_summary_message(today_str, results, dashboard_url)
    alt = f"日次監視完了 {today_str}（全{len(results)}銘柄の{LABEL_DRAWDOWN}・{LABEL_AVG}）"
    logger.info("デイリーサマリー通知を送信")
    return _send_flex_message(alt, bubble, fallback)


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

        sample = [
            {"short_name": "S&P500", "drawdown": 7.2, "avg_cost_ratio": -5.4},
            {"short_name": "SBI・V・S&P500", "drawdown": 7.0, "avg_cost_ratio": 37.1},
            {"short_name": "エヌビディア", "drawdown": 0.0, "avg_cost_ratio": None},
        ]
        ok = _send_flex_message(
            "LINE通知テスト（最高比・平均比）",
            build_daily_summary_flex("テスト送信", sample, "https://itbizmonky.github.io/BaseLine/"),
            build_daily_summary_message("テスト送信", sample, "https://itbizmonky.github.io/BaseLine/"),
        )
        if ok:
            print("✅ テスト送信成功！LINEを確認してください（緑=プラス・赤=マイナス）。")
        else:
            print("❌ テスト送信失敗。ログを確認してください。")
            sys.exit(1)
    else:
        print("使い方: python scripts/notify.py --test")
