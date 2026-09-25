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

from judge import decision_display, format_baseline_ratio, format_drawdown
from portfolio import build_integrated_view
from purchase_history import ACCOUNT_ATTACK, calc_average_cost, filter_by_account, position_purchases, resolve_cost_basis

logger = logging.getLogger(__name__)

PUBLIC_DIR = Path(__file__).parent.parent / "public"

INACTIVE_TAG = '<span class="inactive-tag">（新規購入停止・監視終了）</span>'


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
    positions_display: dict | None = None,
    positions_history: list[dict] | None = None,
    purchase_history: dict | None = None,
) -> None:
    """ダッシュボードHTMLを生成して public/index.html に書き出す。"""
    PUBLIC_DIR.mkdir(exist_ok=True)

    chart_data = _build_chart_data(history, settings, peak, purchase_history)
    summary_html = _build_summary_table(fund_results, settings)
    cards_html = _build_fund_cards(fund_results, navs, peak, triggered, period_info, settings)
    trend_html = _build_trend_summary(fund_results)
    funds_html, funds_note_html = _build_funds_summary(triggered, period_info, settings)
    market_html = _build_market_sentiment(market_display or {})
    positions_html = _build_positions_section(positions_display or {})
    average_cost_html = _build_average_cost_section(settings, purchase_history or {}, navs, history)
    prices = _current_prices(settings, navs, history, positions_display or {})
    integrated_html = _build_integrated_section(build_integrated_view(settings, purchase_history or {}, prices))
    usdjpy_rate = (market_display or {}).get("usdjpy", {}).get("value")
    positions_chart_data = _build_positions_chart_data(positions_history or [], settings, usdjpy_rate, purchase_history or {})

    html = _render_html(
        today_str=today_str,
        period_info=period_info,
        summary_html=summary_html,
        cards_html=cards_html,
        trend_html=trend_html,
        funds_html=funds_html,
        funds_note_html=funds_note_html,
        market_html=market_html,
        positions_html=positions_html,
        average_cost_html=average_cost_html,
        integrated_html=integrated_html,
        chart_data_json=json.dumps(chart_data, ensure_ascii=False),
        positions_chart_data_json=json.dumps(positions_chart_data, ensure_ascii=False),
        settings=settings,
    )

    output_path = PUBLIC_DIR / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"ダッシュボードを生成しました: {output_path}")


# ------------------------------------------------------------------
# チャートデータ構築
# ------------------------------------------------------------------

def _nearest_label_index(target_date: str | None, labels: list[str], label_index: dict, max_gap_days: int = 7) -> int | None:
    """
    target_date が labels に完全一致しない場合、最も近い日付にスナップしたインデックスを返す。
    購入日が対象データの記録開始日の前後1〜数日ずれるケース（例: 保有ポジション追加初日は
    前日分の履歴がまだない）を吸収するための補正。マーカーの日付・金額表示自体は変更せず、
    グラフ上でどのラベル位置に打つかだけを調整する（NAV等のデータを捏造するものではない）。
    差が max_gap_days を超える場合は表示対象外として None を返す。
    """
    if not target_date or not labels:
        return None
    exact = label_index.get(target_date)
    if exact is not None:
        return exact
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return None
    best_idx, best_diff = None, None
    for i, d in enumerate(labels):
        try:
            diff = abs((datetime.strptime(d, "%Y-%m-%d") - target).days)
        except ValueError:
            continue
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = i, diff
    if best_idx is not None and best_diff <= max_gap_days:
        return best_idx
    return None


def _build_chart_data(history: list[dict], settings: dict, peak: dict, purchase_history: dict | None = None) -> dict:
    fund_colors = {f["id"]: f["color"] for f in settings["funds"]}
    fund_names  = {f["id"]: f["short_name"] for f in settings["funds"]}

    recent = history[-180:] if len(history) > 180 else history
    labels = [r["date"] for r in recent]
    label_index = {d: i for i, d in enumerate(labels)}

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

    purchase_markers = {}
    for fund in settings["funds"]:
        fid = fund["id"]
        records = filter_by_account((purchase_history or {}).get(fid, []), ACCOUNT_ATTACK)
        markers = []
        for r in records:
            if not r.get("price"):
                # 約定単価が未記録（null）の実績はマーカーを描画できないためスキップ
                continue
            idx = _nearest_label_index(r["date"], labels, label_index)
            if idx is None:
                # 表示期間（直近180日）外、または近傍に記録がない場合はスキップ
                continue
            markers.append({
                "index": idx,
                "date": r["date"],
                "price": r["price"],
                "category": r.get("category", ""),
                "amount": r.get("amount", 0),
            })
        purchase_markers[fid] = markers

    return {
        "labels": labels,
        "datasets": datasets,
        "tier_lines": tier_lines,
        "purchase_markers": purchase_markers,
    }


def _build_positions_chart_data(positions_history: list[dict], settings: dict, usdjpy_rate: float | None, purchase_history: dict | None = None) -> dict:
    """
    保有ポジション（Tier投資対象外）の推移チャートデータを構築する。
    USD建て銘柄（テスラ等）は、現在のUSD/JPYレートで一律換算した近似値をプロットする
    （為替レートの日次履歴は保持していないため、過去分も現在レートで換算する近似）。
    """
    items = [i for i in settings.get("positions", {}).get("items", []) if not i.get("hidden")]
    recent = positions_history[-180:] if len(positions_history) > 180 else positions_history
    labels = [r["date"] for r in recent]
    label_index = {d: i for i, d in enumerate(labels)}

    datasets = []
    cost_lines = {}
    purchase_markers = {}
    for item in items:
        pid = item["id"]
        currency = item.get("currency", "JPY")
        rate = usdjpy_rate if (currency == "USD" and usdjpy_rate) else 1.0
        data_points = [
            (r.get(pid) * rate) if r.get(pid) is not None else None
            for r in recent
        ]
        datasets.append({
            "id": pid,
            "label": item.get("short_name", pid),
            "data": data_points,
            "borderColor": item.get("color", "#94a3b8"),
            "backgroundColor": item.get("color", "#94a3b8") + "20",
            "borderWidth": 2,
            "pointRadius": 0,
            "tension": 0.3,
            "fill": False,
        })
        records = (purchase_history or {}).get(pid, [])
        cost_basis = resolve_cost_basis(item.get("cost_basis", 0), records)
        cost_lines[pid] = round(cost_basis * rate, 2)

        markers = []
        for p in position_purchases(item, records):
            idx = _nearest_label_index(p["date"], labels, label_index)
            if idx is None or not p.get("price"):
                continue
            markers.append({
                "index": idx,
                "date": p["date"],
                "price": round(p["price"] * rate, 2),
                "amount": p.get("amount"),
                "category": p.get("category", ""),
            })
        purchase_markers[pid] = markers

    return {
        "labels": labels,
        "datasets": datasets,
        "cost_lines": cost_lines,
        "purchase_markers": purchase_markers,
        "is_jpy_converted": usdjpy_rate is not None,
    }


# ------------------------------------------------------------------
# サマリーテーブル HTML
# ------------------------------------------------------------------

def _build_summary_table(fund_results: list[dict], settings: dict) -> str:
    rows = []
    fund_colors = {f["id"]: f["color"] for f in settings["funds"]}
    inactive_ids = {f["id"] for f in settings["funds"] if not f.get("active", True)}

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
            f'  <td data-label="監視銘柄">'
            f'    <div style="display:flex;align-items:center;gap:8px;">'
            f'      <span class="fund-dot" style="background-color:{color};box-shadow:0 0 6px {color}"></span>'
            f'      <span style="font-weight:600;">{r["short_name"]}</span>'
            f'{INACTIVE_TAG if r["fund_id"] in inactive_ids else ""}'
            f'    </div>'
            f'  </td>'
            f'  <td data-label="最高値比 (下落率)"><span class="val-drawdown">{format_drawdown(r["drawdown"])}</span></td>'
            f'  <td data-label="基準日比 (上昇率)">{format_baseline_ratio(r.get("baseline_ratio"))}</td>'
            f'  <td data-label="到達段階"><span class="val-tier">{tier_str}</span></td>'
            f'  <td data-label="システム判定"><span class="status-badge {dec_class}">{dec_emoji} {dec_label}</span></td>'
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
        baseline_ratio = result.get("baseline_ratio")
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
        baseline_ratio_str = format_baseline_ratio(baseline_ratio) if nav is not None else "-"
        inactive_tag = INACTIVE_TAG if not fund.get("active", True) else ""

        tier_bars = _tier_bars(tier, tiers, color)

        card = f"""
<div class="fund-card" style="--fund-color: {color}">
  <div class="fund-card__header">
    <div class="fund-card__name-block">
      <span class="fund-tag" style="border: 1px solid {color}50; color:{color};">{fund['short_name']}</span>{inactive_tag}
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


def _build_positions_section(positions_display: dict) -> str:
    """
    保有ポジション（Tier投資対象外・監視専用）カードのHTMLを生成する。
    含み損益（現在価格と取得単価の差）を表示するだけで、BUY/WAIT判定には使用しない。
    """
    positions_display = {k: v for k, v in positions_display.items() if not v.get("hidden")}
    if not positions_display:
        return '<div class="market-empty">保有ポジションのデータを準備中です。次回の自動実行後に表示されます。</div>'

    cards = []
    for p in positions_display.values():
        level = p.get("level", {})
        currency = p.get("currency", "JPY")
        if currency == "USD":
            value_str = f"{p['value']:,.2f} USD"
            jpy_note = f'<div class="market-card__note">約{p["value_jpy"]:,.0f}円換算</div>'
            cost_str = f"{p['cost_basis']:,.2f} USD"
        else:
            value_str = f"{p['value']:,.0f}円"
            jpy_note = ""
            cost_str = f"{p['cost_basis']:,.0f}円"

        purchase_note = ""
        purchases = p.get("purchases") or []
        if purchases:
            dates_str = "、".join(x["date"] for x in purchases)
            total = sum(x["amount"] for x in purchases if x.get("amount"))
            total_str = f"{total:,.0f}円" if total else "-"
            times_str = f"（{len(purchases)}回）" if len(purchases) > 1 else ""
            purchase_note = f'<div class="market-card__note">取得日: {dates_str} ／ 投入金額: {total_str}{times_str}</div>'

        cards.append(
            f'<div class="market-card" style="border-left: 3px solid {p.get("color", "#94a3b8")}">'
            f'  <div class="market-card__label">{p["short_name"]}</div>'
            f'  <div class="market-card__value">{value_str}</div>'
            f'  {jpy_note}'
            f'  <span class="status-badge {level.get("css", "pos-neutral")}">{level.get("emoji", "")} {level.get("label", "-")}</span>'
            f'  <div class="market-card__note">取得単価: {cost_str} ／ 含み損益: {p["ratio"]:+.1f}%</div>'
            f'  {purchase_note}'
            f'  <div class="market-card__date">{p.get("date", "-")} 時点</div>'
            f'</div>'
        )

    return "\n".join(cards)


def _current_prices(settings: dict, navs: dict, history: list[dict], positions_display: dict) -> dict:
    """
    統合ビューの評価額計算に使う「銘柄ID→現在の基準価額」を作る。
    Tier対象ファンドは当日取得の navs（取得失敗時は history.csv の最新値）、
    保有ポジション系（SBI・V等、hidden含む）は positions_display の値を使う。
    """
    prices = {}
    for f in settings["funds"]:
        fid = f["id"]
        nav = navs.get(fid)
        if nav is None:
            nav = next((r.get(fid) for r in reversed(history) if r.get(fid) is not None), None)
        prices[fid] = nav
    for pid, p in positions_display.items():
        prices[pid] = p.get("value")
    return prices


def _yen(v: float | None) -> str:
    return f"{v:,.0f}円" if v is not None else "算出不可"


_ACCOUNT_LABELS = {"attack": "攻撃フェーズ", "side": "別枠積立", "legacy": "旧つみたてNISA（保持）"}
_ACCOUNT_SHORT = {"attack": "攻撃", "side": "別枠", "legacy": "旧つみたて"}
_ROLE_LABELS = {"core": "コア", "satellite": "サテライト"}


def _ratio_block(title: str, value_html: str, badge: str, formula: str, amounts: str, bar: str, note: str = "") -> str:
    """比率1つ分のブロック（見出し・大きな数値・バッジ・計算式・バー・補足）のHTMLを返す。"""
    return (
        '<div class="sat-hero" style="margin-top:12px;">'
        f'<div class="sat-hero__label">{title}</div>'
        f'<div class="sat-hero__row">{value_html}{badge}</div>'
        f'<div class="market-card__note">計算式: {formula}</div>'
        f'<div class="market-card__note">{amounts}</div>'
        f'{bar}{note}'
        '</div>'
    )


def _bar(share: float, marker: float | None, marker_label: str) -> str:
    """割合バー（塗り＝現在値、黄色の線＝目標または基準）。"""
    fill = max(0.0, min(share, 100.0))
    mk = ""
    if marker is not None:
        m = max(0.0, min(marker, 100.0))
        mk = (
            f'<div class="sat-bar__target" style="left:{m:.1f}%"></div>'
            f'<div class="sat-bar__target-label" style="left:{m:.1f}%">{marker_label}</div>'
        )
    return f'<div class="sat-bar"><div class="sat-bar__fill" style="width:{fill:.1f}%"></div>{mk}</div>'


def _build_integrated_section(view: dict | None) -> str:
    """
    長期ポートフォリオ（保有している全ファンドの合算）の統合ビューのセクションHTMLを生成する。
    「全体」に対するサテライト比率（目標との対比）と、維持判断ルール（比率ベース）の状況、
    グループ別の評価額・全体に占める割合・口座別内訳を表示する（表示専用）。
    view が None（未設定・無効）の場合は空文字を返す。
    """
    if view is None:
        return ""

    groups = view["groups"]
    total = view["total_value"]
    provisional = f'<p class="pf-warn">⚠ {view["provisional"]}</p>' if view.get("provisional") else ""
    all_names = "、".join(n for g in groups for n in g["fund_names"])

    # ---- サテライト比率
    ratio, target = view["satellite_ratio"], view["target_percent"]
    sat_formula = "（" + "＋".join(view["satellite_names"]) + "）÷ 全体"
    if ratio is None:
        sat_block = _ratio_block(
            "サテライト比率", '<span class="sat-hero__value" style="font-size:20px;">算出不可</span>', "",
            sat_formula, "現在の基準価額を取得できない銘柄があるため算出できません（次回の実行で再計算されます）。", "",
        )
    else:
        diff = view["diff_pt"]
        badge = (
            f'<span class="status-badge {"pos-warning" if diff > 0 else "pos-good"}">'
            f'目標より {abs(diff):.1f}pt {"高い" if diff > 0 else "低い"}</span>'
        )
        sat_block = _ratio_block(
            f"サテライト比率（目標 {target:.0f}%）",
            f'<span class="sat-hero__value">{ratio:.1f}%</span>', badge, sat_formula,
            f"{_yen(view['satellite_value'])} ÷ {_yen(total)}",
            _bar(ratio, target, f"目標{target:.0f}%"),
        )

    # ---- 維持判断ルール（比率ベース。例: S&P500比率）
    review_block = ""
    hr = view.get("share_review")
    if hr:
        share, thr, status = hr["share"], hr["threshold_percent"], hr["status"]
        formula = "（" + "＋".join(hr["fund_names"]) + "）÷ 全体"
        if status is None:
            review_block = _ratio_block(
                f'{hr["label"]}比率', '<span class="sat-hero__value" style="font-size:20px;">算出不可</span>', "",
                formula, "現在の基準価額を取得できない銘柄があるため算出できません。", "",
            )
        else:
            css, txt = {
                "keep": ("pos-good", "維持（基準内）"),
                "review": ("pos-warning", "見直し検討"),
                "unset": ("pos-neutral", "基準の数値が未設定"),
            }[status]
            rule = (
                f"基準: {hr['label']}比率が{thr:g}%を超えたら見直しを検討する"
                if thr is not None else
                f"基準: {hr['label']}比率が○%を超えたら見直しを検討する（○はユーザーが決めた数値を config/settings.json の long_term_portfolio.share_review.threshold_percent に設定）"
            )
            note = f"<br>{hr['note']}" if hr.get("note") else ""
            review_block = _ratio_block(
                f'{hr["label"]}比率（維持判断ルール）',
                f'<span class="sat-hero__value">{share:.1f}%</span>',
                f'<span class="status-badge {css}">{txt}</span>', formula,
                f"{_yen(hr['value'])} ÷ {_yen(total)}",
                _bar(share, thr, f"基準{thr:g}%" if thr is not None else ""),
                f'<div class="market-card__note">{rule}（表示のみ。自動売却はしません）{note}</div>',
            )

    # ---- グループ別カード
    cards = []
    for g in groups:
        accounts = []
        for h in g["holdings"]:
            if h["account"] not in accounts:
                accounts.append(h["account"])
        by_account = {a: 0.0 for a in accounts}
        missing = {a: False for a in accounts}
        for h in g["holdings"]:
            if h["value"] is None:
                missing[h["account"]] = True
            else:
                by_account[h["account"]] += h["value"]
        share_txt = f"全体の{g['share']:.1f}%" if g["share"] is not None else "全体比 -"
        costs = [
            f"{_ACCOUNT_SHORT.get(h['account'], h['account'])} {h['avg_cost']:,.0f}円"
            for h in g["holdings"] if h["avg_cost"] is not None
        ]
        cost_txt = "／".join(costs) if costs else "データなし"
        lines = "".join(
            f'<div class="pf-line"><span>{_ACCOUNT_LABELS.get(a, a)}</span>'
            f'<span>{"算出不可" if missing[a] else _yen(by_account[a])}</span></div>'
            for a in accounts
            if len(accounts) > 1 or a != "attack"
        )
        cards.append(
            '<div class="market-card">'
            f'<div class="market-card__label">{g["label"]}<span class="inactive-tag">{_ROLE_LABELS.get(g["role"], g["role"])}</span></div>'
            f'<div class="market-card__value" style="font-size:20px;">{_yen(g["total_value"])}</div>'
            f'<div class="market-card__note">{share_txt}</div>'
            f'{lines}'
            f'<div class="pf-line"><span>平均取得単価</span><span>{cost_txt}</span></div>'
            '</div>'
        )

    return (
        '<section class="section-panel">'
        '<div class="section-title">🏦 長期ポートフォリオ（保有ファンドの合算）</div>'
        '<p class="market-disclaimer">2028年月初のリバランス判断用に、保有している全ファンド（攻撃フェーズ・別枠積立・旧つみたてNISA）の評価額を合算した「全体」に対する割合を表示します。'
        '参考情報であり、BUY/WAITの判定・LINE通知には使用しません。</p>'
        f'<p class="market-disclaimer"><strong>全体</strong> ＝ {all_names} の評価額の合計 ＝ <strong>{_yen(total)}</strong></p>'
        f'{provisional}{sat_block}{review_block}'
        f'<div class="market-grid" style="margin-top:16px;">{"".join(cards)}</div>'
        '<p class="market-disclaimer" style="margin-top:10px;">評価額＝保有口数×現在の基準価額（既存残高を含む）。S&P500の別枠積立・旧つみたてNISA分はSBI・V・S&P500の基準価額で評価。'
        'はじめてのNISA（子供用の別枠）・テスラは含みません。</p>'
        '</section>'
    )


def _build_average_cost_section(settings: dict, purchase_history: dict, navs: dict, history: list[dict]) -> str:
    """
    銘柄ごとの平均取得単価と現在の基準価額を並べ、含み益/含み損を表示するカードのHTMLを生成する。
    約定実績（data/purchase_history.json）のみから算出する表示専用の情報で、
    Tier判定・BUY/WAIT判定・LINE通知には一切使用しない。
    """
    cards = []
    for fund in settings["funds"]:
        fid = fund["id"]
        records = filter_by_account(purchase_history.get(fid, []), ACCOUNT_ATTACK)
        if not fund.get("active", True) and not records:
            continue

        avg = calc_average_cost(records)
        excluded = len(records) - (avg["count"] if avg else 0)
        inactive_tag = INACTIVE_TAG if not fund.get("active", True) else ""
        color = fund.get("color", "#94a3b8")

        current = navs.get(fid)
        if current is None:
            current = next((r.get(fid) for r in reversed(history) if r.get(fid) is not None), None)

        notes = []
        if avg is None:
            value_html = '<div class="market-card__value" style="font-size:16px;">データなし</div>'
            badge_html = ""
            if records:
                notes.append(f"約定単価が未記録の実績{excluded}件のみのため算出できません。")
            else:
                notes.append("約定実績が未登録です（data/purchase_history.json に追記）。")
        else:
            value_html = f'<div class="market-card__value">{avg["avg_cost"]:,.0f}<span class="market-card__unit">円</span></div>'
            if current is None:
                badge_html = ""
            else:
                ratio = (current - avg["avg_cost"]) / avg["avg_cost"] * 100
                if current >= avg["avg_cost"]:
                    badge_html = f'<span class="status-badge pos-good">🟢 含み益 {ratio:+.1f}%</span>'
                else:
                    badge_html = f'<span class="status-badge pos-warning">🔴 含み損 {ratio:+.1f}%</span>'
            current_str = f"{current:,.0f}円" if current is not None else "取得失敗"
            notes.append(f"現在の基準価額: {current_str}")
            notes.append(f"投入金額合計: {avg['total_amount']:,.0f}円 ／ 購入{avg['count']}回")
            if excluded:
                notes.append(f'<span style="color:var(--yellow);">⚠ 約定単価が未記録の実績{excluded}件を除いた暫定値です</span>')
        if fund.get("avg_cost_note"):
            notes.append(fund["avg_cost_note"])

        notes_html = "".join(f'<div class="market-card__note">{n}</div>' for n in notes)
        cards.append(
            f'<div class="market-card" style="border-left: 3px solid {color}">'
            f'  <div class="market-card__label">{fund["short_name"]}{inactive_tag}</div>'
            f'  {value_html}'
            f'  {badge_html}'
            f'  {notes_html}'
            f'</div>'
        )
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
    skip_notes = []
    for fund in settings["funds"]:
        if not fund.get("active", True):
            # 新規購入停止銘柄は原資0円のため資金投入管理の対象外
            continue
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
        for record in triggered.get(fid, []):
            if not record.get("invested", True):
                date_str = record.get("date") or "-"
                skip_notes.append(f'{fund["short_name"]} Tier{record["tier"]} ({date_str}) は投資を見送ったため、投入済み金額に含まれていません。')

    if skip_notes:
        skip_html = "".join(f'<p style="font-size:11px; color:var(--text-mute); margin-bottom:4px;">※{s}</p>' for s in skip_notes)
        note_html += skip_html

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
    positions_html: str,
    average_cost_html: str,
    integrated_html: str,
    chart_data_json: str,
    positions_chart_data_json: str,
    settings: dict,
) -> str:
    phase_label = period_info.get("label", "-")
    days_remaining = period_info.get("days_remaining", 0)
    baseline_date = settings.get("baseline", {}).get("date", "2026-07-07")
    peak_start_date = settings.get("peak_start_date", "2026-08-01")
    phase_type = period_info.get("phase", "none")

    fund_tabs = "".join(
        f'<button class="chart-tab" data-fund="{f["id"]}">{f["short_name"]}{"（停止）" if not f.get("active", True) else ""}</button>'
        for f in settings["funds"]
    )
    positions_tabs = "".join(
        f'<button class="chart-tab" data-position="{item["id"]}">{item.get("short_name", item["id"])}</button>'
        for item in settings.get("positions", {}).get("items", [])
        if not item.get("hidden")
    )

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
.pos-great, .pos-good{{ background: rgba(16,185,129,0.15); color: var(--green); border-color: rgba(16,185,129,0.3); box-shadow: var(--green-glow); }}
.pos-neutral{{ background: rgba(255,255,255,0.05); color: var(--text-mute); border-color: rgba(255,255,255,0.05); }}
.pos-caution{{ background: rgba(245,158,11,0.15); color: var(--yellow); border-color: rgba(245,158,11,0.3); box-shadow: var(--yellow-glow); }}
.pos-warning{{ background: rgba(239,68,68,0.15); color: var(--red); border-color: rgba(239,68,68,0.3); box-shadow: var(--red-glow); }}

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
.inactive-tag{{ font-size: 10px; font-weight: 400; color: var(--text-mute); margin-left: 6px; }}
.sat-hero{{ background: #080a0e; box-shadow: var(--shadow-in); border-radius: var(--radius); padding: 18px 20px; }}
.sat-hero__label{{ font-size: 12px; font-weight: 700; color: var(--text-mute); margin-bottom: 6px; }}
.sat-hero__row{{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.sat-hero__value{{ font-size: 34px; font-weight: 700; font-family: 'Outfit', sans-serif; color: var(--text); }}
.sat-bar{{ position: relative; height: 12px; background: rgba(255,255,255,0.06); border-radius: 6px; margin: 22px 0 8px; }}
.sat-bar__fill{{ height: 100%; background: linear-gradient(90deg, #3b82f6, #8b5cf6); border-radius: 6px; }}
.sat-bar__target{{ position: absolute; top: -5px; width: 2px; height: 22px; background: var(--yellow); }}
.sat-bar__target-label{{ position: absolute; top: -22px; transform: translateX(-50%); font-size: 10px; color: var(--yellow); white-space: nowrap; }}
.pf-warn{{ font-size: 11px; color: var(--yellow); margin-bottom: 12px; line-height: 1.6; }}
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
.chart-tabs{{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }}
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

html, body{{ overflow-x: hidden; }}
.pf-line{{ display: flex; justify-content: space-between; align-items: baseline; gap: 10px; font-size: 12px; padding: 4px 0; border-top: 1px solid rgba(255,255,255,0.04); }}
.pf-line span:first-child{{ color: var(--text-mute); white-space: nowrap; }}
.pf-line span:last-child{{ text-align: right; font-family: 'Inter', monospace; }}
@media(max-width: 768px){{
  .main{{ padding: 14px 10px; gap: 16px; }}
  .section-panel{{ padding: 16px 14px; }}
  .fund-card{{ padding: 16px 14px; }}
  .guide-toggle{{ padding: 14px 16px; }}
  .fund-card__metrics{{ grid-template-columns: 1fr 1fr; }}
  .trend-grid{{ grid-template-columns: 1fr 1fr; }}
  .guide-grid{{ grid-template-columns: 1fr; }}
  .market-grid{{ grid-template-columns: 1fr; }}
}}
@media(max-width: 600px){{
  /* 投資意思決定サマリー: 横スクロールを避けるため、1銘柄1カードの縦積みにする */
  .table-wrapper{{ padding: 4px; overflow-x: visible; }}
  .summary-table{{ min-width: 0; }}
  .summary-table thead{{ display: none; }}
  .summary-table, .summary-table tbody, .summary-table tr, .summary-table td{{ display: block; width: 100%; }}
  .summary-table tr{{ padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  .summary-table tr:last-child{{ border-bottom: none; }}
  .summary-table td{{ display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 5px 6px; border: none; font-size: 13px; }}
  .summary-table td::before{{ content: attr(data-label); color: var(--text-mute); font-size: 11px; white-space: nowrap; }}
  .summary-table td:first-child{{ font-size: 14px; padding-bottom: 6px; }}
  .summary-table td:first-child::before{{ content: none; }}
  .funds-table th, .funds-table td{{ padding: 10px 4px; font-size: 12px; }}
}}
@media(max-width: 480px){{
  .header-inner{{ flex-direction: column; gap: 8px; text-align: center; }}
  .header-sub{{ text-align: center; }}
  .header{{ padding: 12px 14px; }}
  .sat-hero__value{{ font-size: 28px; }}
  .market-card__value{{ font-size: 20px; }}
  .chart-wrapper{{ height: 240px; padding: 8px; }}
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
            <li style="margin-bottom:8px;"><strong>価格決定のタイミング（重要）:</strong> 対象銘柄はいずれも海外資産に投資するため「ブラインド方式」が適用され、発注日の翌営業日の海外市場終値をもとに基準価額が決定されます。発注は当日の締切後キャンセルできないため、Tier到達＝即発注が必ずしも最適とは限りません。</li>
            <li style="margin-bottom:8px;"><strong>保有ポジション（監視専用）について:</strong> ページ下部の「保有ポジション」は、上記のTier判定とは別枠です。階層的な追加投資の対象ではなく、取得単価との差（含み損益）を表示するだけの監視専用の項目です。</li>
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

  <!-- ===== 平均取得単価と含み損益 ===== -->
  <section class="section-panel">
    <div class="section-title">💰 平均取得単価と含み損益</div>
    <p class="market-disclaimer">これまでの約定実績（購入実績）から算出した銘柄別の平均取得単価と、現在の基準価額の比較です。参考情報であり、BUY/WAITの判定・LINE通知には使用しません。</p>
    <div class="market-grid">
      {average_cost_html}
    </div>
  </section>

  <!-- ===== 銘柄詳細カード ===== -->
  {cards_html}

  <!-- ===== 推移チャート ===== -->
  <section class="section-panel">
    <div class="section-title">基準価額の推移とTier閾値</div>
    <div class="chart-tabs" id="chartTabs">
      <button class="chart-tab active" data-fund="all">全銘柄</button>
      {fund_tabs}
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
          <p>1ドルが何円かを表すレートです。各銘柄はいずれも為替ヘッジなしのため、基準価額の変動には「米国株そのものの値動き」と「円ドルレートの変動」の両方が混ざっています。円高が進んでいるときのTier到達は、見た目の下落の一部が為替要因である可能性を示唆します。</p>
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

  <!-- ===== 保有ポジション（監視専用・Tier対象外） ===== -->
  <section class="section-panel">
    <div class="section-title">📦 保有ポジション（監視専用）</div>
    <p class="market-disclaimer">この銘柄は暴落時の階層的追加投資（Tier）の対象ではありません。取得単価との差（含み損益）を監視・通知するだけの保有ポジションです。</p>
    <div class="market-grid">
      {positions_html}
    </div>
    <div class="chart-tabs" id="positionsChartTabs" style="margin-top:20px;">
      <button class="chart-tab active" data-position="all">全ポジション</button>
      {positions_tabs}
    </div>
    <div class="chart-wrapper">
      <canvas id="positionsChart"></canvas>
    </div>
  </section>

  <!-- ===== 長期ポートフォリオ（統合ビュー。最下部） ===== -->
  {integrated_html}

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

function purchaseMarkerColor(category) {{
  if (category === 'Tier1') return '#eab308';
  if (category === 'Tier2') return '#f97316';
  if (category === 'Tier3') return '#ef4444';
  return '#3b82f6';
}}

function buildDatasets(fundFilter) {{
  const priceDatasets = RAW_DATA.datasets
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

  // 「全銘柄」表示時はTier閾値線・約定実績マーカーは描画しない（視認性を優先）
  if (fundFilter === 'all') return priceDatasets;

  const extra = [];

  const tierInfo = RAW_DATA.tier_lines[fundFilter];
  if (tierInfo) {{
    tierInfo.tier_values.forEach((val, i) => {{
      extra.push({{
        label: `Tier${{i + 1}}閾値 (-${{tierInfo.tiers[i]}}%)`,
        data: RAW_DATA.labels.map(() => val),
        borderColor: tierInfo.color,
        borderWidth: 1,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
        shadowBlur: 0,
        isTierLine: true,
      }});
    }});
  }}

  const markers = (RAW_DATA.purchase_markers && RAW_DATA.purchase_markers[fundFilter]) || [];
  if (markers.length > 0) {{
    const arr = new Array(RAW_DATA.labels.length).fill(null);
    const meta = new Array(RAW_DATA.labels.length).fill(null);
    markers.forEach(m => {{ arr[m.index] = m.price; meta[m.index] = m; }});
    extra.push({{
      label: '約定実績',
      data: arr,
      showLine: false,
      pointStyle: 'star',
      pointRadius: arr.map(v => v !== null ? 9 : 0),
      pointHoverRadius: arr.map(v => v !== null ? 11 : 0),
      pointBackgroundColor: meta.map(m => m ? purchaseMarkerColor(m.category) : 'transparent'),
      pointBorderColor: '#fff',
      pointBorderWidth: 1,
      shadowBlur: 0,
      isPurchaseMarker: true,
      markerMeta: meta,
    }});
  }}

  return [...priceDatasets, ...extra];
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
          filter: (item) => item.raw !== null && item.raw !== undefined,
          callbacks: {{
            label: (item) => {{
              if (item.dataset.isPurchaseMarker) {{
                const m = item.dataset.markerMeta[item.dataIndex];
                if (!m) return null;
                return [
                  `★ 約定日: ${{m.date}}`,
                  `　約定単価: ${{m.price.toLocaleString()}}円`,
                  `　区分: ${{m.category}}`,
                  `　投入金額: ${{(m.amount || 0).toLocaleString()}}円`,
                ];
              }}
              return `  ${{item.dataset.label}}: ${{item.raw !== null ? item.raw.toLocaleString() + '円' : '-'}}`;
            }},
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
  document.querySelectorAll('#chartTabs .chart-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderChart(btn.dataset.fund);
}});

renderChart('all');

// ===== 保有ポジション（監視専用）チャート =====
const POSITIONS_RAW_DATA = {positions_chart_data_json};
let positionsChartInstance = null;

function buildPositionsDatasets(positionFilter) {{
  const priceDatasets = POSITIONS_RAW_DATA.datasets
    .filter(ds => positionFilter === 'all' || ds.id === positionFilter)
    .map(ds => ({{ ...ds, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 6, tension: 0.25 }}));

  if (positionFilter === 'all') return priceDatasets;

  const markers = (POSITIONS_RAW_DATA.purchase_markers && POSITIONS_RAW_DATA.purchase_markers[positionFilter]) || [];
  if (markers.length === 0) return priceDatasets;

  const arr = new Array(POSITIONS_RAW_DATA.labels.length).fill(null);
  const meta = new Array(POSITIONS_RAW_DATA.labels.length).fill(null);
  markers.forEach(m => {{ arr[m.index] = m.price; meta[m.index] = m; }});
  const extra = {{
    label: '約定実績',
    data: arr,
    showLine: false,
    pointStyle: 'star',
    pointRadius: arr.map(v => v !== null ? 9 : 0),
    pointHoverRadius: arr.map(v => v !== null ? 11 : 0),
    pointBackgroundColor: '#eab308',
    pointBorderColor: '#fff',
    pointBorderWidth: 1,
    isPurchaseMarker: true,
    markerMeta: meta,
  }};
  return [...priceDatasets, extra];
}}

function renderPositionsChart(positionFilter = 'all') {{
  const canvas = document.getElementById('positionsChart');
  if (!canvas || !POSITIONS_RAW_DATA.datasets || POSITIONS_RAW_DATA.datasets.length === 0) return;
  const ctx = canvas.getContext('2d');
  const datasets = buildPositionsDatasets(positionFilter);

  if (positionsChartInstance) {{
    positionsChartInstance.data.datasets = datasets;
    positionsChartInstance.update('active');
    return;
  }}

  positionsChartInstance = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: POSITIONS_RAW_DATA.labels, datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 500, easing: 'easeOutQuart' }},
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{
          display: positionFilter === 'all',
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
          filter: (item) => item.raw !== null && item.raw !== undefined,
          callbacks: {{
            label: (item) => {{
              if (item.dataset.isPurchaseMarker) {{
                const m = item.dataset.markerMeta[item.dataIndex];
                if (!m) return null;
                return [
                  `★ 取得日: ${{m.date}}`,
                  `　取得単価: ${{m.price.toLocaleString()}}円`,
                  `　投入金額: ${{(m.amount || 0).toLocaleString()}}円`,
                ];
              }}
              return `  ${{item.dataset.label}}: ${{item.raw !== null ? item.raw.toLocaleString() + '円' : '-'}}`;
            }},
          }},
        }},
      }},
      scales: {{
        x: {{ grid: {{ color: 'rgba(255,255,255,0.02)' }}, ticks: {{ color: '#64748b', font: {{ size: 9, family: 'Inter' }}, maxTicksLimit: 10 }} }},
        y: {{ grid: {{ color: 'rgba(255,255,255,0.02)' }}, ticks: {{ color: '#64748b', font: {{ size: 9, family: 'Inter' }}, callback: (v) => v !== null ? v.toLocaleString() + '円' : '' }} }},
      }},
    }},
  }});
}}

const positionsChartTabsEl = document.getElementById('positionsChartTabs');
if (positionsChartTabsEl) {{
  positionsChartTabsEl.addEventListener('click', (e) => {{
    const btn = e.target.closest('.chart-tab');
    if (!btn) return;
    document.querySelectorAll('#positionsChartTabs .chart-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderPositionsChart(btn.dataset.position);
  }});
}}
renderPositionsChart('all');
</script>
</body>
</html>
"""
