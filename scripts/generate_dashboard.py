"""
generate_dashboard.py
=====================
HTMLダッシュボード生成モジュール。

monitor.py から呼び出され、全銘柄の判定結果をもとに
public/index.html を生成する。

生成されたHTMLはGitHub Pagesで公開される。
Chart.js はCDN経由で読み込む（依存ライブラリ不要）。
スマホ対応のレスポンシブデザイン。
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

from judge import decision_display, format_drawdown

logger = logging.getLogger(__name__)

PUBLIC_DIR = Path(__file__).parent.parent / "public"


def generate(
    today_str: str,
    navs: dict,
    peak: dict,
    fund_results: list[dict],
    history: list[dict],
    period_info: dict,
    settings: dict,
    triggered: dict,
    market_display: dict | None = None,
) -> None:
    """ダッシュボードHTMLを生成して public/index.html に書き出す。"""
    PUBLIC_DIR.mkdir(exist_ok=True)

    chart_data = _build_chart_data(history, settings, peak)
    summary_html = _build_summary_table(fund_results, settings)
    cards_html = _build_fund_cards(fund_results, navs, peak, triggered, period_info, settings)
    trend_html = _build_trend_summary(fund_results)
    funds_html, funds_note_html = _build_funds_summary(triggered, period_info, settings)
    market_html = _build_market_sentiment(market_display or {})

    html = _render_html(
        today_str=today_str,
        period_info=period_info,
        summary_html=summary_html,
        cards_html=cards_html,
        trend_html=trend_html,
        funds_html=funds_html,
        funds_note_html=funds_note_html,
        market_html=market_html,
        chart_data_json=json.dumps(chart_data, ensure_ascii=False),
        settings=settings,
    )

    output_path = PUBLIC_DIR / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"ダッシュボードを生成しました: {output_path}")


# ------------------------------------------------------------------
# チャートデータ構築
# ------------------------------------------------------------------

def _build_chart_data(history: list[dict], settings: dict, peak: dict) -> dict:
    fund_colors = {f["id"]: f["color"] for f in settings["funds"]}
    fund_names  = {f["id"]: f["short_name"] for f in settings["funds"]}
    
    recent = history[-180:] if len(history) > 180 else history
    labels = [r["date"] for r in recent]

    datasets = []
    for fund in settings["funds"]:
        fid = fund["id"]
        data_points = [r.get(fid) for r in recent]
        datasets.append({
            "id": fid,
            "label": fund_names[fid],
            "data": data_points,
            "borderColor": fund_colors[fid],
            "backgroundColor": fund_colors[fid] + "20",
            "borderWidth": 2,
            "pointRadius": 0,
            "tension": 0.3,
            "fill": False,
        })

    tier_lines = {}
    for fund in settings["funds"]:
        fid = fund["id"]
        peak_val = peak.get(fid, {}).get("value")
        if peak_val is None:
            continue
        tier_lines[fid] = {
            "peak": peak_val,
            "tier_values": [
                round(peak_val * (1 - t / 100), 0)
                for t in fund["tiers"]
            ],
            "tiers": fund["tiers"],
            "color": fund["color"],
        }

    return {
        "labels": labels,
        "datasets": datasets,
        "tier_lines": tier_lines,
    }


# ------------------------------------------------------------------
# サマリーテーブル HTML
# ------------------------------------------------------------------

def _build_summary_table(fund_results: list[dict], settings: dict) -> str:
    rows = []
    fund_colors = {f["id"]: f["color"] for f in settings["funds"]}
    
    for r in fund_results:
        color = fund_colors.get(r["fund_id"], "#ffffff")
        decision = r.get("decision", "HOLD")
        info = decision_display(decision)
        dec_emoji = f"{info['emoji']} {info['tag']}"
        dec_label = info["label"]
        dec_class = f"badge-{info['css']}"

        tier_val = r["tier"]
        tier_str = f"Tier {tier_val}" if tier_val > 0 else "未到達"

        rows.append(
            f'<tr>'
            f'  <td>'
            f'    <div style="display:flex;align-items:center;gap:8px;">'
            f'      <span class="fund-dot" style="background-color:{color};box-shadow:0 0 6px {color}"></span>'
            f'      <span style="font-weight:600;">{r["short_name"]}</span>'
            f'    </div>'
            f'  </td>'
            f'  <td><span class="val-drawdown">{format_drawdown(r["drawdown"])}</span></td>'
            f'  <td>{r["baseline_ratio"]:+.1f}%</td>'
            f'  <td><span class="val-tier">{tier_str}</span></td>'
            f'  <td><span class="status-badge {dec_class}">{dec_emoji} {dec_label}</span></td>'
            f'</tr>'
        )
    return "\n".join(rows)


# ------------------------------------------------------------------
# 銘柄カード HTML
# ------------------------------------------------------------------

def _build_fund_cards(
    fund_results: list[dict],
    navs: dict,
    peak: dict,
    triggered: dict,
    period_info: dict,
    settings: dict,
) -> str:
    html_parts = []
    result_map = {r["fund_id"]: r for r in fund_results}

    for fund in settings["funds"]:
        fid = fund["id"]
        result = result_map.get(fid, {})
        nav = navs.get(fid)
        peak_info = peak.get(fid, {})
        peak_val = peak_info.get("value")
        peak_date = peak_info.get("date", "-")
        
        tier = result.get("tier", 0)
        drawdown = result.get("drawdown", 0.0)
        baseline_nav = result.get("baseline_nav", 0)
        baseline_ratio = result.get("baseline_ratio", 0.0)
        decision = result.get("decision", "HOLD")
        
        tiers = fund["tiers"]
        color = fund["color"]
        
        info = decision_display(decision)
        dec_emoji = f"{info['emoji']} {info['tag']}"
        dec_label = info["label"]
        dec_class = f"dec-{info['css']}"

        next_tier_text = _next_tier_text(tier, drawdown, tiers)

        nav_str = f"{nav:,.0f}円" if nav is not None else "取得失敗"
        peak_str = f"{peak_val:,.0f}円" if peak_val is not None else "未記録"
        baseline_str = f"{baseline_nav:,.0f}円" if baseline_nav > 0 else "未設定"
        
        drawdown_str = format_drawdown(drawdown) if nav is not None else "-"
        baseline_ratio_str = f"{baseline_ratio:+.1f}%" if nav is not None else "-"

        tier_bars = _tier_bars(tier, tiers, color)

        card = f"""
<div class="fund-card" style="--fund-color: {color}">
  <div class="fund-card__header">
    <div class="fund-card__name-block">
      <span class="fund-tag" style="border: 1px solid {color}50; color:{color};">{fund['short_name']}</span>
      <div class="fund-card__name">{fund['name']}</div>
    </div>
    <div class="status-badge {dec_class}">{dec_emoji} {dec_label}</div>
  </div>

  <div class="fund-card__metrics">
    <div class="metric-item">
      <div class="metric-label">現在価格</div>
      <div class="metric-value">{nav_str}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">設定来最高価格</div>
      <div class="metric-value">{peak_str}</div>
      <div class="metric-sub">{peak_date} 記録</div>
    </div>
    <div class="metric-item metric-item--highlight">
      <div class="metric-label">最高値からの下落率</div>
      <div class="metric-value drawdown">{drawdown_str}</div>
      <div class="metric-sub">{next_tier_text}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">判定基準日価格</div>
      <div class="metric-value">{baseline_str}</div>
      <div class="metric-sub">比: <span style="color:var(--text); font-weight:600;">{baseline_ratio_str}</span></div>
    </div>
  </div>

  <div class="tier-indicator-title">購入目安 (Tier) 到達状況</div>
  <div class="tier-progress">
    {tier_bars}
  </div>
</div>
"""
        html_parts.append(card)
    return "\n".join(html_parts)


def _next_tier_text(tier: int, drawdown: float, tiers: list) -> str:
    next_tier_idx = tier
    if next_tier_idx < len(tiers):
        gap = tiers[next_tier_idx] - drawdown
        return f"Tier{next_tier_idx + 1} (-{tiers[next_tier_idx]}%) まであと {gap:.1f}pt"
    return "Tier3上限超過済"


def _tier_bars(tier: int, tiers: list, color: str) -> str:
    bars = []
    labels = ["Tier1", "Tier2", "Tier3"]
    for i, (label, threshold) in enumerate(zip(labels, tiers), start=1):
        active = "active" if tier >= i else ""
        bars.append(
            f'<div class="tier-bar {active}" style="{"--bar-color:" + color if active else ""}">'
            f'  <span class="tier-bar__label">{label}</span>'
            f'  <span class="tier-bar__val">-{threshold}%</span>'
            f'</div>'
        )
    return "\n".join(bars)


# ------------------------------------------------------------------
# その他サマリー HTML
# ------------------------------------------------------------------

def _build_trend_summary(fund_results: list[dict]) -> str:
    items = []
    for r in fund_results:
        items.append(
            f'<div class="trend-item">'
            f'  <span class="trend-name">{r["short_name"]}</span>'
            f'  <span class="trend-val">5日: <span class="trend-icon">{r.get("trend_5d", "→")}</span></span>'
            f'  <span class="trend-val">20日: <span class="trend-icon">{r.get("trend_20d", "→")}</span></span>'
            f'</div>'
        )
    return "\n".join(items)


def _build_market_sentiment(market_display: dict) -> str:
    """
    市場心理カード（VIX・米10年金利・USD/JPY）のHTMLを生成する。
    あくまで参考情報であり、BUY/WAIT判定には一切使用しない。
    データがまだない場合は準備中メッセージを返す。
    """
    cards = []

    vix = market_display.get("vix")
    if vix:
        level = vix.get("level", {})
        cards.append(
            f'<div class="market-card">'
            f'  <div class="market-card__label">VIX指数（恐怖指数）</div>'
            f'  <div class="market-card__value">{vix["value"]:.2f}</div>'
            f'  <span class="status-badge {level.get("css", "vix-normal")}">{level.get("label", "-")}</span>'
            f'  <div class="market-card__note">{level.get("note", "")}</div>'
            f'  <div class="market-card__date">{vix.get("date", "-")} 時点</div>'
            f'</div>'
        )

    us10y = market_display.get("us10y")
    if us10y:
        direction = us10y.get("direction", {})
        diff = direction.get("diff")
        diff_str = f'{diff:+.3f}pt' if diff is not None else "-"
        cards.append(
            f'<div class="market-card">'
            f'  <div class="market-card__label">米10年国債利回り</div>'
            f'  <div class="market-card__value">{us10y["value"]:.2f}<span class="market-card__unit">%</span></div>'
            f'  <div class="market-card__direction"><span class="trend-icon">{direction.get("arrow", "→")}</span> 前回比 {diff_str}</div>'
            f'  <div class="market-card__note">成長株（FANG+・SOXなど）は金利上昇局面で下がりやすい傾向があります。</div>'
            f'  <div class="market-card__date">{us10y.get("date", "-")} 時点</div>'
            f'</div>'
        )

    usdjpy = market_display.get("usdjpy")
    if usdjpy:
        direction = usdjpy.get("direction", {})
        diff = direction.get("diff")
        diff_str = f'{diff:+.2f}円' if diff is not None else "-"
        cards.append(
            f'<div class="market-card">'
            f'  <div class="market-card__label">USD/JPY（ドル円）</div>'
            f'  <div class="market-card__value">{usdjpy["value"]:.2f}<span class="market-card__unit">円</span></div>'
            f'  <div class="market-card__direction"><span class="trend-icon">{direction.get("arrow", "→")}</span> 前回比 {diff_str}</div>'
            f'  <div class="market-card__note">円高が進むと、米国株の実力が変わらなくても基準価額は下がって見えます。</div>'
            f'  <div class="market-card__date">{usdjpy.get("date", "-")} 時点</div>'
            f'</div>'
        )

    if not cards:
        return '<div class="market-empty">市場心理データを準備中です。次回の自動実行後に表示されます。</div>'

    return "\n".join(cards)


def _build_funds_summary(triggered: dict, period_info: dict, settings: dict) -> tuple[str, str]:
    phase_key = period_info.get("phase", "phase2")
    is_fallback = phase_key not in ("phase2", "phase3")
    if is_fallback:
        phase_key = "phase2"

    fallback_label = settings.get("periods", {}).get(phase_key, {}).get("label", "②期間")
    note_html = (
        f'<p style="font-size:11px; color:var(--text-mute); margin-bottom:10px;">'
        f'※現在は「{period_info.get("label", "-")}」のため、参考として{fallback_label}の金額を暫定表示しています。'
        f'</p>'
        if is_fallback else ""
    )

    from judge import calc_remaining_funds
    rows = []
    for fund in settings["funds"]:
        fid = fund["id"]
        rem = calc_remaining_funds(fid, triggered, phase_key, settings)
        rows.append(
            f'<tr>'
            f'  <td><span style="color:{fund["color"]}; font-weight:600;">{fund["short_name"]}</span></td>'
            f'  <td>{rem["invested"]:,}円</td>'
            f'  <td>{rem["remaining"]:,}円</td>'
            f'  <td>{rem["total"]:,}円</td>'
            f'</tr>'
        )
    return "\n".join(rows), note_html


# ------------------------------------------------------------------
# HTML レンダリング
# ------------------------------------------------------------------

def _render_html(
    today_str: str,
    period_info: dict,
    summary_html: str,
    cards_html: str,
    trend_html: str,
    funds_html: str,
    funds_note_html: str,
    market_html: str,
    chart_data_json: str,
    settings: dict,
) -> str:
    phase_label = period_info.get("label", "-")
    days_remaining = period_info.get("days_remaining", 0)
    baseline_date = settings.get("baseline", {}).get("date", "2026-07-07")
    peak_start_date = settings.get("peak_start_date", "2026-08-01")
    phase_type = period_info.get("phase", "none")

    if phase_type == "before_start":
        period_display_str = f"📅 状態: ②期間開始まであと{days_remaining}日"
    elif phase_type == "ended":
        period_display_str = "📅 状態: 監視期間終了"
    else:
        period_display_str = f"📅 現在期間: {phase_label} (期限まで残{days_remaining}日)"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>投資判断ダッシュボード</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;600;700&family=Outfit:wght@500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
/* ===== CSS Reset & Variables ===== */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg: #0b0d13;
  --panel-bg: #121622;
  --shadow-out: 6px 6px 14px #06070a, -6px -6px 14px #1e253a;
  --shadow-in: inset 3px 3px 8px #06070a, inset -3px -3px 8px #1e253a;
  --text: #f8fafc;
  --text-mute: #64748b;
  --text-dark: #334155;
  --green: #10b981;
  --green-glow: 0 0 12px rgba(16, 185, 129, 0.4);
  --yellow: #f59e0b;
  --yellow-glow: 0 0 12px rgba(245, 158, 11, 0.4);
  --red: #ef4444;
  --red-glow: 0 0 12px rgba(239, 68, 68, 0.4);
  --blue: #3b82f6;
  --radius: 20px;
  --inner-radius: 10px;
}}
html{{scroll-behavior:smooth}}
body{{
  font-family:'Noto Sans JP','Inter',sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  background-image:
    radial-gradient(circle at 10% 15%, rgba(59,130,246,0.06) 0%, transparent 40%),
    radial-gradient(circle at 90% 85%, rgba(139,92,246,0.04) 0%, transparent 40%);
  padding-bottom: 40px;
}}

/* ===== Header ===== */
.header{{
  background: rgba(11, 13, 19, 0.85); backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.03); padding: 16px 20px;
  position: sticky; top: 0; z-index: 100;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}}
.header-inner{{
  max-width: 900px; margin: 0 auto;
  display: flex; justify-content: space-between; align-items: center;
}}
.header-title{{
  font-family: 'Outfit', sans-serif; font-size: 20px; font-weight: 700;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  display: flex; align-items: center; gap: 8px;
}}
.header-sub{{
  font-size: 12px; color: var(--text-mute); text-align: right; line-height: 1.5;
}}

/* ===== Main Layout ===== */
.main{{ max-width: 900px; margin: 0 auto; padding: 20px 16px; display: flex; flex-direction: column; gap: 24px; }}

/* ===== Section Panels (Neumorphism) ===== */
.section-panel{{
  background: var(--panel-bg); box-shadow: var(--shadow-out); border-radius: var(--radius);
  padding: 24px; border: 1px solid rgba(255, 255, 255, 0.02);
}}
.section-title{{
  font-size: 15px; font-weight: 700; color: var(--text);
  display: flex; align-items: center; gap: 8px; margin-bottom: 16px;
  border-left: 4px solid var(--blue); padding-left: 10px;
}}

/* ===== Accordion Guide Panel ===== */
.guide-toggle{{
  width: 100%; display: flex; justify-content: space-between; align-items: center;
  background: var(--panel-bg); box-shadow: var(--shadow-out); border: 1px solid rgba(255, 255, 255, 0.02);
  border-radius: var(--radius); padding: 16px 24px; color: var(--text); font-size: 14px;
  font-weight: 700; cursor: pointer; text-align: left; transition: all 0.2s;
}}
.guide-toggle:hover{{ background: rgba(255,255,255,0.02); }}
.guide-content{{
  max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out, padding 0.3s ease-out;
  background: rgba(18, 22, 34, 0.5); border-radius: 0 0 var(--radius) var(--radius);
  padding: 0 24px; box-shadow: inset 0 4px 10px rgba(0,0,0,0.2);
}}
.guide-grid{{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.guide-item{{ font-size: 12px; line-height: 1.8; color: var(--text-mute); }}
.guide-item h4{{ font-size: 13px; color: var(--text); margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }}
.guide-item ul{{ list-style: none; }}
.guide-item li{{ margin-bottom: 10px; }}
.guide-item code{{ background: #000; padding: 2px 5px; border-radius: 4px; color: var(--red); font-family: monospace; }}
.toggle-icon{{ transition: transform 0.3s; font-size: 10px; }}

/* ===== Status Badges & Glows ===== */
.status-badge{{
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700;
  border: 1px solid transparent; background: rgba(255,255,255,0.03);
}}
.badge-buy, .dec-buy{{ background: rgba(16,185,129,0.15); color: var(--green); border-color: rgba(16,185,129,0.3); box-shadow: var(--green-glow); }}
.badge-wait, .dec-wait{{ background: rgba(245,158,11,0.15); color: var(--yellow); border-color: rgba(245,158,11,0.3); box-shadow: var(--yellow-glow); }}
.badge-hold, .dec-hold{{ background: rgba(255,255,255,0.05); color: var(--text-mute); border-color: rgba(255,255,255,0.05); }}
.badge-high, .dec-high{{ background: rgba(59,130,246,0.15); color: var(--blue); border-color: rgba(59,130,246,0.3); box-shadow: 0 0 12px rgba(59,130,246,0.4); }}
.vix-hot{{ background: rgba(59,130,246,0.15); color: var(--blue); border-color: rgba(59,130,246,0.3); }}
.vix-normal{{ background: rgba(16,185,129,0.15); color: var(--green); border-color: rgba(16,185,129,0.3); }}
.vix-caution{{ background: rgba(245,158,11,0.15); color: var(--yellow); border-color: rgba(245,158,11,0.3); }}
.vix-fear{{ background: rgba(249,115,22,0.18); color: #f97316; border-color: rgba(249,115,22,0.35); box-shadow: 0 0 10px rgba(249,115,22,0.3); }}
.vix-crash{{ background: rgba(239,68,68,0.18); color: var(--red); border-color: rgba(239,68,68,0.35); box-shadow: var(--red-glow); }}

/* ===== Neumorphic Table ===== */
.table-wrapper{{ background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--radius); padding: 12px; overflow-x: auto; }}
.summary-table{{ width: 100%; border-collapse: collapse; font-size: 13px; min-width: 500px; }}
.summary-table th{{
  text-align: left; padding: 10px 12px; color: var(--text-mute);
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
}}
.summary-table td{{ padding: 14px 12px; border-bottom: 1px solid rgba(255,255,255,0.02); font-family: 'Inter', monospace; }}
.summary-table tr:last-child td{{ border-bottom: none; }}
.summary-table td:first-child{{ font-family: 'Noto Sans JP', sans-serif; }}
.fund-dot{{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; }}
.val-drawdown{{ color: var(--red); font-weight: 700; }}
.val-tier{{ font-weight: 600; color: var(--blue); }}

/* ===== Fund Cards ===== */
.fund-card{{
  background: var(--panel-bg); box-shadow: var(--shadow-out); border-radius: var(--radius);
  padding: 24px; border: 1px solid rgba(255, 255, 255, 0.02);
  transition: transform 0.2s, box-shadow 0.2s;
  border-left: 5px solid var(--fund-color, #3b82f6);
}}
.fund-card:hover{{ transform: translateY(-2px); box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
.fund-card__header{{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; }}
.fund-tag{{
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 10px; font-weight: 700; background: rgba(255,255,255,0.02);
  font-family: 'Outfit', sans-serif;
}}
.fund-card__name{{ font-size: 15px; font-weight: 700; margin-top: 4px; }}
.fund-card__metrics{{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }}
.metric-item{{ background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--inner-radius); padding: 12px; }}
.metric-item--highlight{{ border: 1px solid rgba(239, 68, 68, 0.2); }}
.metric-label{{ font-size: 9px; color: var(--text-mute); font-weight: 700; text-transform: uppercase; margin-bottom: 4px; }}
.metric-value{{ font-size: 16px; font-weight: 700; font-family: 'Inter', monospace; }}
.metric-value.drawdown{{ color: var(--red); text-shadow: 0 0 8px rgba(239,68,68,0.2); }}
.metric-sub{{ font-size: 10px; color: var(--text-mute); margin-top: 4px; }}

/* ===== Tier Progress Indicators ===== */
.tier-indicator-title{{ font-size: 11px; font-weight: 700; color: var(--text-mute); margin-bottom: 8px; text-transform: uppercase; }}
.tier-progress{{ display: flex; gap: 10px; }}
.tier-bar{{
  flex: 1; padding: 8px 12px; border-radius: 8px;
  background: #080a0e; box-shadow: var(--shadow-in);
  display: flex; justify-content: space-between; align-items: center;
  font-size: 11px; color: var(--text-mute); transition: all 0.3s;
  border: 1px solid transparent;
}}
.tier-bar.active{{
  color: #fff; font-weight: 700;
  background: var(--bar-color); border-color: transparent;
  box-shadow: 0 0 10px var(--bar-color);
  text-shadow: 0 1px 2px rgba(0,0,0,0.5);
}}
.tier-bar__label{{ font-family: 'Outfit', sans-serif; }}
.tier-bar__val{{ font-family: 'Inter', monospace; font-weight: 700; }}

/* ===== Trend & Chart Panels ===== */
.chart-tabs{{ display: flex; gap: 8px; margin-bottom: 16px; }}
.chart-tab{{
  padding: 6px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.02);
  background: var(--panel-bg); box-shadow: var(--shadow-out); color: var(--text-mute);
  font-size: 11px; font-weight: 600; cursor: pointer; transition: all 0.15s;
}}
.chart-tab.active{{
  background: rgba(59,130,246,0.15); border-color: rgba(59,130,246,0.3);
  color: #60a5fa; box-shadow: 0 0 10px rgba(59,130,246,0.15);
}}
.chart-wrapper{{ position: relative; height: 260px; background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--radius); padding: 12px; }}

.trend-grid{{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
.trend-item{{
  background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--inner-radius);
  padding: 12px; display: flex; flex-direction: column; gap: 4px;
}}
.trend-name{{ font-size: 11px; font-weight: 700; color: var(--text-mute); }}
.trend-val{{ font-size: 12px; font-family: 'Inter', monospace; color: var(--text); }}
.trend-icon{{ font-weight: 700; color: var(--blue); }}

/* ===== 市場心理カード ===== */
.market-disclaimer{{ font-size: 12px; color: var(--text-mute); margin-bottom: 16px; line-height: 1.6; }}
.market-grid{{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }}
.market-card{{
  background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--inner-radius);
  padding: 16px; display: flex; flex-direction: column; gap: 6px;
}}
.market-card__label{{ font-size: 11px; font-weight: 700; color: var(--text-mute); text-transform: uppercase; }}
.market-card__value{{ font-size: 22px; font-weight: 700; font-family: 'Inter', monospace; color: var(--text); }}
.market-card__unit{{ font-size: 13px; font-weight: 600; color: var(--text-mute); margin-left: 2px; }}
.market-card__direction{{ font-size: 12px; color: var(--text); font-family: 'Inter', monospace; }}
.market-card__note{{ font-size: 11px; color: var(--text-mute); line-height: 1.6; margin-top: 2px; }}
.market-card__date{{ font-size: 10px; color: var(--text-mute); opacity: 0.7; margin-top: 4px; }}
.market-empty{{ grid-column: 1 / -1; text-align: center; font-size: 12px; color: var(--text-mute); padding: 20px; }}

.funds-table{{ width:100%; border-collapse:collapse; font-size:13px; }}
.funds-table th{{ text-align:left; padding:10px; color:var(--text-mute); font-size:11px; font-weight:600; border-bottom:1px solid rgba(255,255,255,0.02); }}
.funds-table td{{ padding:12px 10px; border-bottom:1px solid rgba(255,255,255,0.02); font-family:'Inter',monospace; }}
.funds-table tr:last-child td{{ border-bottom: none; }}
.funds-table td:first-child{{ font-family:'Noto Sans JP',sans-serif; }}

/* ===== Footer ===== */
.footer{{ text-align: center; padding: 30px; font-size: 11px; color: var(--text-mute); border-top: 1px solid rgba(255, 255, 255, 0.02); margin-top: 40px; }}

@media(max-width: 768px){{
  .fund-card__metrics{{ grid-template-columns: 1fr 1fr; }}
  .trend-grid{{ grid-template-columns: 1fr 1fr; }}
  .guide-grid{{ grid-template-columns: 1fr; }}
  .market-grid{{ grid-template-columns: 1fr; }}
}}
@media(max-width: 480px){{
  .header-inner{{ flex-direction: column; gap: 8px; text-align: center; }}
  .header-sub{{ text-align: center; }}
  .summary-table th, .summary-table td{{ padding: 10px 6px; font-size: 11px; }}
  .tier-progress{{ flex-direction: column; gap: 6px; }}
  .metric-value{{ font-size: 14px; }}
}}
</style>
</head>
<body>

<header class="header">
  <div class="header-inner">
    <div class="header-title">🛡️ VALOR SHIELD <span style="font-weight:400; font-size:14px; color:var(--text-mute); margin-left:4px;">暴落監視</span></div>
    <div class="header-sub">
      更新: {today_str} JST &nbsp;｜&nbsp; 基準日: {baseline_date}<br>
      <span style="color:var(--blue); font-weight:600;">{period_display_str}</span>
    </div>
  </div>
</header>

<main class="main">

  <!-- ===== 投資判断クイックガイド (アコーディオン) ===== -->
  <section class="guide-section">
    <button class="guide-toggle" id="guideToggle">
      <span>💡 投資シグナルとルールの見方 (初心者向けガイド)</span>
      <span class="toggle-icon" style="display:inline-block; transition:transform 0.2s;">▼</span>
    </button>
    <div class="guide-content" id="guideContent">
      <div class="guide-grid" style="padding: 16px 0;">
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">🟢 シグナル(判定)の読み方</h4>
          <ul style="padding-left: 0;">
            <li style="margin-bottom:8px;"><span class="status-badge badge-buy">🟢 BUY</span> <strong>購入推奨:</strong> 目安の下落率(Tier)に到達し、現在価格が基準日から暴騰していない状態。</li>
            <li style="margin-bottom:8px;"><span class="status-badge badge-wait">🟡 WAIT</span> <strong>上昇待機:</strong> 下落目安には達していますが、判定基準価格(直近安値付近)から価格が少し上昇(+5.0%超)しているため、手動注文の前に一時様子見を推奨する状態。</li>
            <li style="margin-bottom:8px;"><span class="status-badge badge-hold">⚪ HOLD</span> <strong>様子見:</strong> 下落率が小さく、購入目安に達していない平常の状態。</li>
            <li style="margin-bottom:8px;"><span class="status-badge badge-high">🔵 HIGH</span> <strong>高値更新中:</strong> 設定来高値を本日更新した、または高値圏を維持している絶好調の状態。</li>
          </ul>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">📐 暴落判定ルール</h4>
          <ul style="padding-left: 0;">
            <li style="margin-bottom:8px;"><strong>最高値からの下落率:</strong> {peak_start_date}以降に記録した最高値から、現在の価格が何％下がっているかを表します（例: <code>-15.0%</code>）。この下落が設定した各Tierに達するとシグナルが発動します。</li>
            <li style="margin-bottom:8px;"><strong>判定基準価格:</strong> 暴落初期や安値時の価格を基準とし、そこから<code>+5.0%</code>以上価格が急上昇した場合は、高値掴みを避けるため一時的に <code>WAIT</code> と判定されます。</li>
            <li style="margin-bottom:8px;"><strong>注意:</strong> 実際の買付注文は、SBI証券等の画面から手動で発注する必要があります。</li>
          </ul>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 総合サマリー ===== -->
  <section class="section-panel">
    <div class="section-title">投資意思決定サマリー</div>
    <div class="table-wrapper">
      <table class="summary-table">
        <thead>
          <tr>
            <th>監視銘柄</th>
            <th>最高値比 (下落率)</th>
            <th>基準日比 (上昇率)</th>
            <th>到達段階</th>
            <th>システム判定</th>
          </tr>
        </thead>
        <tbody>
          {summary_html}
        </tbody>
      </table>
    </div>
  </section>

  <!-- ===== 銘柄詳細カード ===== -->
  {cards_html}

  <!-- ===== 推移チャート ===== -->
  <section class="section-panel">
    <div class="section-title">基準価額の推移とTier閾値</div>
    <div class="chart-tabs" id="chartTabs">
      <button class="chart-tab active" data-fund="all">全銘柄</button>
      <button class="chart-tab" data-fund="fang">FANG+</button>
      <button class="chart-tab" data-fund="sox">SOX半導体</button>
      <button class="chart-tab" data-fund="sp500">S&P500</button>
      <button class="chart-tab" data-fund="orkan">オルカン</button>
    </div>
    <div class="chart-wrapper">
      <canvas id="mainChart"></canvas>
    </div>
  </section>

  <!-- ===== 市場心理（参考情報） ===== -->
  <section class="section-panel">
    <div class="section-title">🌡️ 市場心理（参考情報）</div>
    <p class="market-disclaimer">これらは売買の判断基準ではありません。BUY/WAITの判定は引き続き価格（Tier）のみで行われます。判定に迷ったときの参考としてご覧ください。</p>
    <button class="guide-toggle" id="marketGuideToggle">
      <span>💡 3つの指標の見方 (初心者向けガイド)</span>
      <span class="toggle-icon" style="display:inline-block; transition:transform 0.2s;">▼</span>
    </button>
    <div class="guide-content" id="marketGuideContent">
      <div class="guide-grid" style="padding: 16px 0;">
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">😨 VIX指数（恐怖指数）</h4>
          <p>市場参加者の不安・警戒感の大きさを数値化した指標です。数値が高いほど「市場全体が動揺している」状態を表します。FANG+やSOXのような銘柄は、市場全体が動揺すると相場全体以上に大きく下がりやすい性質があります。VIXが高いときのTier到達は「全面安に巻き込まれている」可能性、VIXが平常のままのTier到達は「個別要因での下落」の可能性を示唆します。</p>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">💰 米10年国債利回り</h4>
          <p>米国政府が10年間お金を借りる際の金利です。FANG+やSOXのような成長株は、将来の利益を現在価値に割り引いて株価が形成されるため、金利が上がると株価が下がりやすい関係が知られています。金利が急上昇しているときのTier到達は、金利要因による調整の可能性を示唆します。</p>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">💱 USD/JPY（ドル円）</h4>
          <p>1ドルが何円かを表すレートです。4銘柄はいずれも為替ヘッジなしのため、基準価額の変動には「米国株そのものの値動き」と「円ドルレートの変動」の両方が混ざっています。円高が進んでいるときのTier到達は、見た目の下落の一部が為替要因である可能性を示唆します。</p>
        </div>
      </div>
    </div>
    <div class="market-grid">
      {market_html}
    </div>
  </section>

  <!-- ===== トレンドサマリー ===== -->
  <section class="section-panel">
    <div class="section-title">直近モメンタム (トレンド)</div>
    <div class="trend-grid">
      {trend_html}
    </div>
  </section>

  <!-- ===== 残資金サマリー ===== -->
  <section class="section-panel">
    <div class="section-title">資金投入管理状況 &nbsp;<span style="font-size:12px; font-weight:normal; color:var(--text-mute);">({phase_label})</span></div>
    {funds_note_html}
    <div class="table-wrapper">
      <table class="funds-table">
        <thead>
          <tr>
            <th>銘柄</th>
            <th>投入済金額</th>
            <th>残枠資金</th>
            <th>原資合計</th>
          </tr>
        </thead>
        <tbody>
          {funds_html}
        </tbody>
      </table>
    </div>
  </section>

</main>

<footer class="footer">
  <p>データ出典：日本経済新聞 電子版（日経電子版）投資信託ページ ｜ 本ダッシュボードは投資判断の支援のみを行います。投資は自己責任で行ってください。</p>
</footer>

<script>
const RAW_DATA = {chart_data_json};

let chartInstance = null;

// アコーディオンの開閉制御（汎用化して複数箇所で使い回す）
function setupAccordion(toggleId, contentId) {{
  const toggle = document.getElementById(toggleId);
  const content = document.getElementById(contentId);
  if (!toggle || !content) return;
  const icon = toggle.querySelector('.toggle-icon');
  toggle.addEventListener('click', () => {{
    if (content.style.maxHeight && content.style.maxHeight !== '0px') {{
      content.style.maxHeight = '0px';
      icon.style.transform = 'rotate(0deg)';
    }} else {{
      content.style.maxHeight = content.scrollHeight + 'px';
      icon.style.transform = 'rotate(180deg)';
    }}
  }});
}}
setupAccordion('guideToggle', 'guideContent');
setupAccordion('marketGuideToggle', 'marketGuideContent');

function buildDatasets(fundFilter) {{
  return RAW_DATA.datasets
    .filter(ds => fundFilter === 'all' || ds.id === fundFilter)
    .map(ds => ({{
      ...ds,
      borderWidth: 2.5,
      pointRadius: 0,
      pointHoverRadius: 6,
      tension: 0.25,
      shadowColor: ds.borderColor,
      shadowBlur: 8,
    }}));
}}

function renderChart(fundFilter = 'all') {{
  const ctx = document.getElementById('mainChart').getContext('2d');
  const datasets = buildDatasets(fundFilter);

  if (chartInstance) {{
    chartInstance.data.datasets = datasets;
    chartInstance.update('active');
    return;
  }}

  // カスタムプラグインで線の発光エフェクトを描画
  const shadowPlugin = {{
    id: 'shadowPlugin',
    beforeDatasetsDraw(chart, args, options) {{
      const {{ ctx }} = chart;
      ctx.save();
      chart.data.datasets.forEach((dataset, index) => {{
        const meta = chart.getDatasetMeta(index);
        if (!meta.hidden && dataset.shadowBlur > 0) {{
          ctx.shadowColor = dataset.shadowColor;
          ctx.shadowBlur = dataset.shadowBlur;
        }}
      }});
    }},
    afterDatasetsDraw(chart, args, options) {{
      chart.ctx.restore();
    }}
  }};

  chartInstance = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: RAW_DATA.labels,
      datasets,
    }},
    plugins: [shadowPlugin],
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 500, easing: 'easeOutQuart' }},
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ 
          display: fundFilter === 'all', 
          position: 'top',
          labels: {{ color: '#94a3b8', font: {{ size: 11, family: 'Inter' }}, boxWidth: 12, usePointStyle: true }} 
        }},
        tooltip: {{
          backgroundColor: 'rgba(11, 13, 20, 0.95)',
          titleColor: '#94a3b8', 
          bodyColor: '#f8fafc',
          borderColor: 'rgba(255,255,255,0.08)', 
          borderWidth: 1,
          padding: 10,
          bodyFont: {{ family: 'Inter' }},
          titleFont: {{ family: 'Inter' }},
          callbacks: {{
            label: (item) => `  ${{item.dataset.label}}: ${{item.raw !== null ? item.raw.toLocaleString() + '円' : '-'}}`,
          }},
        }},
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(255,255,255,0.02)' }},
          ticks: {{ color: '#64748b', font: {{ size: 9, family: 'Inter' }}, maxTicksLimit: 10 }},
        }},
        y: {{
          grid: {{ color: 'rgba(255,255,255,0.02)' }},
          ticks: {{ 
            color: '#64748b', 
            font: {{ size: 9, family: 'Inter' }},
            callback: (v) => v !== null ? v.toLocaleString() + '円' : '' 
          }},
        }},
      }},
    }},
  }});
}}

document.getElementById('chartTabs').addEventListener('click', (e) => {{
  const btn = e.target.closest('.chart-tab');
  if (!btn) return;
  document.querySelectorAll('.chart-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderChart(btn.dataset.fund);
}});

renderChart('all');
</script>
</body>
</html>
"""
