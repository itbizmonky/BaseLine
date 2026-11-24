"""
generate_dashboard.py
=====================
HTMLダッシュボード生成モジュール。

monitor.py から呼び出され、全保有資産（投資信託・米国株）の前日比・最高値比・平均取得単価比・
Tier到達状況をもとに public/index.html を生成する。

生成されたHTMLはGitHub Pagesで公開される。
Chart.js はCDN経由で読み込む（依存ライブラリ不要）。
スマホ対応のレスポンシブデザイン。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from assets import KIND_FUND, all_assets
from funding import load_status
from groups import build_groups
from judge import format_drawdown
from portfolio import build_integrated_view
from purchase_history import calc_average_cost, cost_records, purchase_markers

logger = logging.getLogger(__name__)

PUBLIC_DIR = Path(__file__).parent.parent / "public"

ROLE_LABELS = {"core": "コア", "satellite": "サテライト", "separate": "別会計"}


def _fmt_price(value: float | None, currency: str) -> str:
    """価格を通貨に応じて整形する（投資信託は円/1万口、米国株はUSD/株）。"""
    if value is None:
        return "取得失敗"
    return f"{value:,.2f} USD" if currency == "USD" else f"{value:,.0f}円"


def _role_tag(role: str) -> str:
    return f'<span class="inactive-tag role-tag">（{ROLE_LABELS.get(role, role)}）</span>'


def _current_prices(results: list[dict], history: list[dict]) -> dict:
    """
    統合ビューの評価額計算に使う「資産ID→現在値」を作る。
    取得できた値（米国株は取得失敗時の前回値を含む）を使い、投資信託が取得失敗なら history.csv の最新値で補う。
    """
    prices = {}
    for r in results:
        price = r.get("price")
        if price is None and r["kind"] == KIND_FUND:
            price = next((row.get(r["asset_id"]) for row in reversed(history) if row.get(r["asset_id"]) is not None), None)
        prices[r["asset_id"]] = price
    return prices


def generate(
    today_str: str,
    results: list[dict],
    peak: dict,
    history: list[dict],
    positions_history: list[dict],
    settings: dict,
    triggered: dict,
    market_display: dict | None = None,
    quotes: dict | None = None,
    purchase_history: dict | None = None,
) -> None:
    """ダッシュボードHTMLを生成して public/index.html に書き出す。"""
    PUBLIC_DIR.mkdir(exist_ok=True)

    assets = all_assets(settings)
    purchases = purchase_history or {}
    usdjpy = (market_display or {}).get("usdjpy", {}).get("value")

    chart_data = _build_chart_data(assets, history, positions_history, settings, peak, usdjpy, purchases)
    groups = build_groups(settings, purchases, results, usdjpy, today_str)
    summary_html = _build_summary_table(groups)
    view = build_integrated_view(settings, purchases, _current_prices(results, history), usdjpy)
    cards_html = _build_asset_cards(results, settings, purchases, view, groups)
    market_html = _build_market_sentiment(market_display or {})
    integrated_html = _build_integrated_section(view, groups)
    funding_html = _build_funding_section(load_status())
    ops_html = _build_monthly_ops_section(settings, today_str)
    banner_html = _build_alert_banner(results)

    html = _render_html(
        today_str=today_str,
        banner_html=banner_html,
        summary_html=summary_html,
        cards_html=cards_html,
        market_html=market_html,
        funding_html=funding_html,
        integrated_html=integrated_html,
        ops_html=ops_html,
        chart_data_json=json.dumps(chart_data, ensure_ascii=False),
        settings=settings,
        assets=assets,
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
    購入日が対象データの記録開始日の前後1〜数日ずれるケースを吸収するための補正。マーカーの日付・金額表示
    自体は変更せず、グラフ上でどのラベル位置に打つかだけを調整する（価格データを捏造するものではない）。
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


def _build_chart_data(
    assets: list[dict],
    history: list[dict],
    positions_history: list[dict],
    settings: dict,
    peak: dict,
    usdjpy: float | None,
    purchase_history: dict,
) -> dict:
    """
    全保有資産の推移チャートデータ。横軸は history.csv の日付（直近180日）。
    米国株（USD）は data（USD）と data_jpy（円換算）の両方を持つ。円換算は、その日の USD/JPY
    （positions_history.csv の usdjpy 列）があればそれ、なければ現在のレートによる近似。
    """
    recent = history[-180:] if len(history) > 180 else history
    labels = [r["date"] for r in recent]
    label_index = {d: i for i, d in enumerate(labels)}
    pos_by_date = {r["date"]: r for r in positions_history}

    datasets, tier_lines, markers_by_asset = [], {}, {}
    for a in assets:
        aid = a["id"]
        color = a["color"]
        ds = {
            "id": aid,
            "label": a["short_name"],
            "currency": a["currency"],
            "borderColor": color,
            "backgroundColor": color + "20",
            "borderWidth": 2,
            "pointRadius": 0,
            "tension": 0.3,
            "fill": False,
        }
        if a["kind"] == KIND_FUND:
            ds["data"] = [r.get(aid) for r in recent]
        else:
            usd = [(pos_by_date.get(d) or {}).get(aid) for d in labels]
            rates = [(pos_by_date.get(d) or {}).get("usdjpy") or usdjpy for d in labels]
            ds["data"] = usd
            ds["data_jpy"] = [round(v * rt) if v is not None and rt else None for v, rt in zip(usd, rates)]
        datasets.append(ds)

        peak_val = peak.get(aid, {}).get("value")
        if peak_val is not None and a["tiers"]:
            digits = 2 if a["currency"] == "USD" else 0
            tier_lines[aid] = {
                "peak": peak_val,
                "tier_values": [round(peak_val * (1 - t / 100), digits) for t in a["tiers"]],
                "tiers": a["tiers"],
                "color": color,
            }

        markers = []
        for m in purchase_markers(purchase_history.get(aid, [])):
            idx = _nearest_label_index(m["date"], labels, label_index)
            if idx is None:
                continue  # 表示期間（直近180日）外、または近傍に記録がない場合はスキップ
            markers.append({
                "index": idx,
                "date": m["date"],
                "price": m["price"],
                "category": m["category"],
                "amount": m["amount"] or 0,
                "currency": m["currency"],
            })
        markers_by_asset[aid] = markers

    return {
        "labels": labels,
        "datasets": datasets,
        "tier_lines": tier_lines,
        "purchase_markers": markers_by_asset,
    }


# ------------------------------------------------------------------
# 投資意思決定サマリー HTML
# ------------------------------------------------------------------

def _tier_status(r: dict) -> tuple[str, str]:
    """Tier到達状況の（表示文言, CSSクラス）。しきい値が未設定・取得失敗の場合はそれを示す。"""
    if r.get("drawdown") is None:
        return "取得失敗", "tier-none"
    if not r.get("thresholds"):
        return "Tier未設定", "tier-none"
    if r["tier"] > 0:
        return f"Tier{r['tier']} 到達", "tier-hit"
    return "未到達", "tier-ok"


def _gain_html(gain: float | None) -> str:
    if gain is None:
        return "-"
    return f'<span style="color:var(--{"green" if gain >= 0 else "red"});font-weight:600;">{gain:+.1f}%</span>'


def _group_header_row(group: dict, colspan: int, extra: str = "") -> str:
    """枠の見出し行（積立設定の枠は月額の合計と、設定の開始時期の注記つき）。"""
    monthly = f' <span class="group-sub">月{group["monthly_total"]:,}円</span>' if group.get("monthly_total") else ""
    note = f' <span class="group-sub">※{group["note"]}</span>' if group.get("note") else ""
    return f'<tr class="group-row"><td colspan="{colspan}">{group["label"]}{monthly}{note}{extra}</td></tr>'


def _tier_short(text: str) -> str:
    """スマホ用に短くしたTier表示（「Tier1 到達」→「Tier1」、「Tier未設定」→「未設定」）。"""
    return text.replace(" 到達", "").replace("Tier未設定", "未設定")


def _build_summary_table(groups: list[dict]) -> str:
    """
    投資意思決定サマリー。5つの枠ごとに見出しをつけ、銘柄ごとの前日比・平均取得単価比・Tier到達を1銘柄1行で表示する。
    銘柄名の下に小さく補足（区分・補足タグ・月額・前回値）を出す。スマホではTierを短い表示（t-short）にする。
    """
    rows = []
    for g in groups:
        if not g["lines"]:
            continue
        rows.append(_group_header_row(g, 4))
        for r in g["lines"]:
            tier_text, tier_css = _tier_status(r)
            stale = '<span class="inactive-tag">（前回値）</span>' if r.get("stale") else ""
            tag = f'<span class="inactive-tag">（{r["tag"]}）</span>' if r.get("tag") else ""
            monthly = f'<span class="group-sub">月{r["monthly"]:,}円</span>' if r.get("monthly") else ""
            sub = f'{_role_tag(r["role"])}{tag}{monthly}{stale}'
            rows.append(
                f'<tr>'
                f'<td data-label="銘柄"><div class="nm">'
                f'<div class="nm-main"><span class="fund-dot" style="background-color:{r["color"]};box-shadow:0 0 6px {r["color"]}"></span>'
                f'<span class="nm-name">{r["short_name"]}</span></div>'
                f'<div class="nm-sub">{sub}</div></div></td>'
                f'<td class="num" data-label="前日比">{_gain_html(r.get("day_change"))}</td>'
                f'<td class="num" data-label="平均取得単価比">{_gain_html(r.get("avg_cost_ratio"))}</td>'
                f'<td class="num" data-label="Tier到達"><span class="val-tier {tier_css}">'
                f'<span class="t-full">{tier_text}</span><span class="t-short">{_tier_short(tier_text)}</span></span></td>'
                f'</tr>'
            )
    return "\n".join(rows)


def _build_alert_banner(results: list[dict]) -> str:
    """
    ページ最上部の注意バナー。Tierに到達している銘柄（クリックで該当の詳細が開く）と、取得失敗・前回値の資産を1〜2行で示す。
    """
    hit = [r for r in results if r.get("drawdown") is not None and r.get("tier", 0) > 0]
    bad = [r for r in results if r.get("price") is None or r.get("stale")]
    lines = []
    if hit:
        chips = "・".join(
            f'<a class="bn-link" href="#asset-{r["asset_id"]}">{r["short_name"]}（Tier{r["tier"]}）</a>' for r in hit
        )
        lines.append(f'<div class="bn-line bn-hit">⚠ Tier到達中: {chips}</div>')
    else:
        lines.append('<div class="bn-line bn-ok">✅ Tier到達なし</div>')
    if bad:
        names = "・".join(r["short_name"] for r in bad)
        lines.append(f'<div class="bn-line bn-warn">⚠ 取得失敗・前回値: {names}</div>')
    return f'<section class="alert-banner" id="alertBanner">{"".join(lines)}</section>'


# ------------------------------------------------------------------
# 銘柄カード HTML（全保有資産）
# ------------------------------------------------------------------

def _build_asset_cards(results: list[dict], settings: dict, purchase_history: dict, view: dict | None = None, groups: list[dict] | None = None) -> str:
    baseline_dates = settings.get("baseline", {}).get("dates", {})
    holdings = {r["id"]: r for r in ((view or {}).get("rows", []) + (view or {}).get("separate", []))}
    member_of: dict[str, list[str]] = {}
    for g in groups or []:
        for ln in g["lines"]:
            member_of.setdefault(ln["asset_id"], []).append(g["label"])
    default_start = settings.get("peak_start_date", "")
    html_parts = []

    for r in results:
        aid, currency, color = r["asset_id"], r["currency"], r["color"]
        tiers = r["thresholds"]
        start = baseline_dates.get(aid, default_start)
        avg = calc_average_cost(cost_records(purchase_history.get(aid, [])))
        avg_str = _fmt_price(avg["avg_cost"], currency) if avg else "購入実績なし"
        drawdown = r.get("drawdown")
        tier_text, tier_css = _tier_status(r)

        price_str = _fmt_price(r.get("price"), currency)
        stale_note = f'<div class="metric-sub">※取得失敗のため{r["price_date"]}の値</div>' if r.get("stale") else ""
        peak_str = _fmt_price(r.get("peak_value"), currency) if r.get("peak_value") is not None else "未記録"
        peak_sub = f'{r.get("peak_date") or "-"} 記録（起点 {start}）'
        held = holdings.get(aid)
        if held and held.get("value") is not None:
            value_str = f"{held['value']:,.0f}円"
            share = held.get("share")
            value_sub = f"全体の {share:.1f}%" if share is not None else "別会計（比率の分母に含めない）"
        else:
            value_str, value_sub = "-", "保有なし／評価不可"

        if tiers and drawdown is not None:
            next_text = _next_tier_text(r["tier"], tiers, r.get("peak_value"), currency)
            tier_section = (
                '<div class="tier-indicator-title">下落の目安 (Tier) 到達状況</div>'
                f'<div class="tier-progress">{_tier_bars(r["tier"], tiers, color)}</div>'
            )
        else:
            next_text = "Tier未設定"
            tier_section = '<div class="market-card__note">Tierのしきい値が未設定のため、到達状況は表示しません（config/settings.json の tiers で設定します）。</div>'

        dd_text = format_drawdown(drawdown) if drawdown is not None else "-"
        html_parts.append(f"""
<details class="asset-acc" id="asset-{aid}" style="--fund-color: {color}">
  <summary class="asset-sum">
    <span class="fund-dot" style="background-color:{color};box-shadow:0 0 6px {color}"></span>
    <span class="as-name-block"><span class="as-name">{r['short_name']}</span><span class="as-price">{price_str}</span></span>
    <span class="as-dd">{dd_text}</span>
    <span class="as-tier val-tier {tier_css}"><span class="t-full">{tier_text}</span><span class="t-short">{_tier_short(tier_text)}</span></span>
    <span class="as-chev">▾</span>
  </summary>
<div class="fund-card" style="--fund-color: {color}">
  <div class="fund-card__header">
    <div class="fund-card__name-block">
      <span class="fund-tag" style="border: 1px solid {color}50; color:{color};">{r['short_name']}</span>{_role_tag(r['role'])}
      <div class="fund-card__name">{r['name']}</div>
      <div class="metric-sub" style="margin-top:4px;">枠: {"・".join(member_of.get(aid, [])) or "-"}</div>
    </div>
  </div>

  <div class="fund-card__metrics">
    <div class="metric-item">
      <div class="metric-label">現在値</div>
      <div class="metric-value">{price_str}</div>
      <div class="metric-sub">前日比: {_gain_html(r.get('day_change'))}</div>
      {stale_note}
    </div>
    <div class="metric-item">
      <div class="metric-label">設定来最高値</div>
      <div class="metric-value">{peak_str}</div>
      <div class="metric-sub">{peak_sub}</div>
    </div>
    <div class="metric-item metric-item--highlight">
      <div class="metric-label">最高値比（下落率）</div>
      <div class="metric-value drawdown">{format_drawdown(drawdown) if drawdown is not None else '-'}</div>
      <div class="metric-sub">{next_text}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">平均取得単価</div>
      <div class="metric-value">{avg_str}</div>
      <div class="metric-sub">比: {_gain_html(r.get('avg_cost_ratio'))}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">評価額（構成比）</div>
      <div class="metric-value">{value_str}</div>
      <div class="metric-sub">{value_sub}</div>
    </div>
  </div>

  {tier_section}
</div>
</details>
""")
    return "\n".join(html_parts)


def _next_tier_text(tier: int, tiers: list, peak: float | None, currency: str) -> str:
    """次のTierに達する価格（最高値 × (1 − しきい値)）を表示する。例: 「次のTier1 (-11%): 40,700円」。"""
    if tier >= len(tiers):
        return "Tier3上限超過済"
    threshold = tiers[tier]
    if not peak:
        return f"次のTier{tier + 1} (-{threshold}%)"
    price = peak * (1 - threshold / 100)
    return f"次のTier{tier + 1} (-{threshold}%): {_fmt_price(price, currency)}"


def _tier_bars(tier: int, tiers: list, color: str) -> str:
    bars = []
    for i, threshold in enumerate(tiers, start=1):
        active = "active" if tier >= i else ""
        bars.append(
            f'<div class="tier-bar {active}" style="{"--bar-color:" + color if active else ""}">'
            f'  <span class="tier-bar__label">Tier{i}</span>'
            f'  <span class="tier-bar__val">-{threshold}%</span>'
            f'</div>'
        )
    return "\n".join(bars)


# ------------------------------------------------------------------
# 市場心理 HTML
# ------------------------------------------------------------------

def _build_market_sentiment(market_display: dict) -> str:
    """
    市場心理カード（VIX・米10年金利・USD/JPY）のHTMLを生成する。
    あくまで参考情報であり、Tierの判定・LINE通知には一切使用しない。
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
            f'  <div class="market-card__note">成長株（半導体・テック株など）は金利上昇局面で下がりやすい傾向があります。</div>'
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


# ------------------------------------------------------------------
# 資金投入管理状況 HTML（月数・使用率だけ。金額は公開しない）
# ------------------------------------------------------------------

def _usage_card(label: str, percent: float) -> str:
    return (
        '<div class="market-card">'
        f'<div class="market-card__label">{label}</div>'
        f'<div class="market-card__value">{percent:.1f}<span class="market-card__unit">%</span></div>'
        f'{_bar(percent)}'
        '</div>'
    )


def _build_funding_section(status: dict | None) -> str:
    """原資で積立が続けられる月数と、NISA枠の使用率（金額は載せない）。status は data/funding_status.json。"""
    if not status:
        body = '<div class="market-empty">資金管理のデータを準備中です。約定履歴の取り込み（update_purchases.py）後に表示されます。</div>'
    else:
        cards = []
        capital = status.get("capital")
        if capital:
            months = capital.get("months_remaining")
            months_html = (
                f'<div class="market-card__value">{months:.1f}<span class="market-card__unit">か月</span></div>'
                if months is not None else '<div class="market-card__value" style="font-size:16px;">算出不可</div>'
            )
            cards.append(
                '<div class="market-card">'
                '<div class="market-card__label">原資で積立が続けられる月数</div>'
                f'{months_html}'
                '</div>'
            )
            cards.append(_usage_card("原資の使用率", capital["used_percent"]))
        nisa = status.get("nisa", {})
        year = nisa.get("year", "")
        cards.append(_usage_card(f"成長投資枠の使用率（{year}年）", nisa.get("growth_percent", 0.0)))
        cards.append(_usage_card(f"つみたて投資枠の使用率（{year}年）", nisa.get("tsumitate_percent", 0.0)))
        body = (
            f'<div class="market-grid two">{"".join(cards)}</div>'
            '<p class="market-disclaimer" style="margin-top:10px;">'
            '月数＝原資の残額 ÷ 現金積立の月額（追加買付も原資から賄うため、実際の月数は変わります）。'
            '原資の使用率＝原資から出した買付（現金積立・追加買付・個別株）の累計 ÷ 原資。'
            'NISA枠の使用率＝年間枠（成長240万円・つみたて120万円）に対する約定の累計（子供用・個別株を含む）。'
            f'{status.get("as_of", "-")} 時点（約定履歴を取り込んだときに更新）。金額は公開していません。</p>'
        )

    return (
        '<section class="section-panel" id="sec-funding">'
        '<div class="section-title">資金投入管理状況</div>'
        f'{body}'
        '</section>'
    )


# ------------------------------------------------------------------
# 長期ポートフォリオ HTML
# ------------------------------------------------------------------

def _yen(v: float | None) -> str:
    return f"{v:,.0f}円" if v is not None else "算出不可"


def _bar(share: float) -> str:
    """割合バー（塗り＝割合）。"""
    fill = max(0.0, min(share, 100.0))
    return f'<div class="sat-bar"><div class="sat-bar__fill" style="width:{fill:.1f}%"></div></div>'


def _build_integrated_section(view: dict | None, groups: list[dict] | None = None) -> str:
    """
    長期ポートフォリオ（保有資産の合算）のセクションHTMLを生成する（表示専用）。
    評価額の大きい順に、コア／サテライトの区分・評価額・全体に占める割合・口座別の内訳を表示する。
    子供用（別会計）は比率の分母に含めず、別に表示する。目標・上限は置かない。
    """
    if view is None:
        return ""

    total = view["total_value"]
    if total is None:
        names = "、".join(view["missing"])
        summary = (
            f'<p class="market-disclaimer">現在値を取得できない資産（{names}）があるため、全体の評価額と比率は算出できません。'
            '次回の実行で再計算されます。</p>'
        )
    else:
        summary = (
            '<div class="sat-hero">'
            '<div class="sat-hero__label">サテライト比率（コア以外の合計 ÷ 全体）</div>'
            f'<div class="sat-hero__row"><span class="sat-hero__value">{view["satellite_share"]:.1f}%</span>'
            f'<span class="status-badge pos-neutral">コア {view["core_share"]:.1f}% ／ サテライト {view["satellite_share"]:.1f}%</span></div>'
            f'<div class="market-card__note">{_yen(view["satellite_value"])} ÷ {_yen(total)}（目標・上限は置かず、表示のみ）</div>'
            f'{_bar(view["satellite_share"])}'
            '</div>'
        )

    rows = []
    for g in groups or []:
        if not g["lines"]:
            continue
        sub_share = f'（{g["share"]:.1f}%）' if g["share"] is not None else ("（比率の分母に含めない）" if g["key"] == "child" else "")
        rows.append(_group_header_row(g, 4, f' <span class="group-sub">／ 小計 {_yen(g["value"])}{sub_share}</span>'))
        for ln in sorted(g["lines"], key=lambda x: -(x["value"] or 0)):
            share = (ln["value"] / total * 100) if (total and ln["value"] is not None and g["key"] != "child") else None
            share_cell = f'<div class="pf-share"><span>{share:.1f}%</span>{_bar(share)}</div>' if share is not None else "-"
            role_label = ROLE_LABELS.get(ln["role"], ln["role"])
            tag = f'<span class="inactive-tag">（{ln["tag"]}）</span>' if ln.get("tag") else ""
            rows.append(
                '<tr>'
                f'<td data-label="銘柄"><div class="nm"><div class="nm-main">'
                f'<span class="fund-dot" style="background-color:{ln["color"]}"></span>'
                f'<span class="nm-name">{ln["short_name"]}</span></div>'
                f'<div class="nm-sub"><span class="show-sm inactive-tag">（{role_label}）</span>{tag}</div></div></td>'
                f'<td class="hide-sm" data-label="区分">{role_label}</td>'
                f'<td class="num" data-label="評価額">{_yen(ln["value"])}</td>'
                f'<td class="num" data-label="全体に占める割合">{share_cell}</td>'
                '</tr>'
            )
    table = (
        '<div class="table-wrapper"><table class="summary-table">'
        '<thead><tr><th>銘柄（枠ごと・評価額の大きい順）</th><th class="hide-sm">区分</th><th class="num">評価額</th>'
        '<th class="num"><span class="th-full">全体に占める割合</span><span class="th-short">割合</span></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )

    return (
        '<section class="section-panel" id="sec-portfolio">'
        '<div class="section-title">🏦 長期ポートフォリオ（保有資産の合算）</div>'
        '<p class="market-disclaimer">保有している全資産（投資信託・米国株・旧つみたてNISAを含む）の評価額を合算し、コア（オルカン・S&amp;P500系）とサテライト（それ以外）の割合を表示します。'
        '参考情報であり、Tierの判定・LINE通知には使用しません。</p>'
        f'{summary}{table}'
        '<p class="market-disclaimer" style="margin-top:10px;">評価額＝保有口数（株数）×現在値。保有口数は約定履歴から集計し、米国株はUSD株価×USD/JPYで円換算しています。</p>'
        '</section>'
    )


# ------------------------------------------------------------------
# 毎月の運用手順 HTML
# ------------------------------------------------------------------

def _schedule_phase(block: dict, today_str: str) -> tuple[list[dict], dict | None]:
    """
    積立設定のブロックから、今日（YYYY-MM-DD）に有効な積立内容と、次に切り替わる予定の内容を返す。
    block に phases（[{"from": 日付, "items": [...], "note": 任意}]）があれば from が今日以前で最新のものを現行、
    今日より後で最も近いものを予定とする。phases がなければ block["items"] を現行とする。
    """
    phases = sorted(block.get("phases") or [], key=lambda p: p["from"])
    if not phases:
        return block.get("items", []), None
    current = [p for p in phases if p["from"] <= today_str]
    upcoming = [p for p in phases if p["from"] > today_str]
    return (current[-1]["items"] if current else phases[0]["items"]), (upcoming[0] if upcoming else None)


def _build_monthly_ops_section(settings: dict, today_str: str = "") -> str:
    """
    毎月の運用手順のサマリ（開閉式ガイド）のHTMLを生成する。
    自動で行われること／毎月の作業／下落アラートが来たときの対応を、手順どおりに並べる。
    """
    def schedule_line(key: str) -> str:
        b = (settings.get("monthly_schedule") or {}).get(key)
        if not b:
            return ""

        def fmt(items: list[dict]) -> str:
            text = "／".join(
                f'{i["fund"]} {i["amount"]:,}円' + (f'（{i["account_label"]}）' if i.get("account_label") else "")
                for i in items
            )
            return f'{text}（月 {sum(i["amount"] for i in items):,}円）'

        cur_items, upcoming = _schedule_phase(b, today_str or "9999-12-31")
        line = (
            f'<li><strong>{b["label"]}</strong>（{b.get("payment", "")}）: 毎月{b["order_day"]}日に発注、約定は{b.get("execution_day", b["order_day"])}日前後 → '
            f'{fmt(cur_items)}'
        )
        if upcoming:
            y, m, d = upcoming["from"].split("-")
            note = upcoming.get("note") or f"{int(m)}/{int(d)}発注分から"
            line += f'<br><span style="color:var(--yellow);">▶ {note}の新構成: {fmt(upcoming["items"])}</span>'
        return line + '</li>'

    schedule_html = schedule_line("cash") + schedule_line("card")

    return (
        '<section class="guide-section">'
        '<button class="guide-toggle" id="opsToggle">'
        '<span>📅 運用手順（サマリ）</span>'
        '<span class="toggle-icon" style="display:inline-block; transition:transform 0.2s;">▼</span>'
        '</button>'
        '<div class="guide-content" id="opsContent">'
        '<div class="guide-grid" style="padding: 16px 0;">'

        '<div class="guide-item">'
        '<h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">🤖 自動で行われること（何もしなくてよい）</h4>'
        '<ul style="padding-left: 0;">'
        '<li>平日の朝7時ごろ、基準価額・株価の取得、最高値比・Tier判定、ダッシュボードの更新が自動で走ります。</li>'
        '<li>平均取得単価・評価額・コア／サテライトの比率は、約定履歴から自動で計算されます。</li>'
        '<li>積立対象の銘柄で新しいTierに到達すると、LINEに下落アラートが届きます。毎朝の日次サマリー（最高値比・平均取得単価比）も届きます。</li>'
        '</ul>'
        '<h4 style="color:var(--text); margin: 14px 0 8px; font-weight:700;">📅 毎月の作業（3ステップ）</h4>'
        '<ul style="padding-left: 0;">'
        '<li><strong>① 積立日</strong>（SBI証券の積立設定どおりに自動で約定。操作は不要です）</li>'
        f'{schedule_html}'
        '<li><strong>② 約定が確定したら</strong>（積立日の数日後）: SBI証券から<strong>約定履歴CSV</strong>（投資信託・米国株）をダウンロードし、'
        '<code>python scripts/update_purchases.py 投信CSV 米国株CSV</code> を実行します。取り込み予定を確認（y）→ 書き込み → コミット・プッシュまで自動です。</li>'
        '<li><strong>③ 翌営業日以降</strong>: ダッシュボードの反映を確認します。見る場所は「投資意思決定サマリー」「資金投入管理状況」「長期ポートフォリオ」です。</li>'
        '</ul>'
        '</div>'

        '<div class="guide-item">'
        '<h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">🚨 下落アラートのLINE通知が来たとき</h4>'
        '<ul style="padding-left: 0;">'
        '<li>通知は「気づき」です。LINEには最高値比と平均取得単価比だけが届きます。購入額や売買の指示は含みません。</li>'
        '<li>買うかどうかは自分で判断します。追加買付（個別株・手動買付）に枠はなく、<strong>原資から賄います</strong>（残りは「資金投入管理状況」で確認）。</li>'
        '<li>買う場合はSBI証券で<strong>手動発注</strong>します（自動発注はしません）。約定後は上の②と同じ手順でCSVを取り込みます。</li>'
        '</ul>'
        '<h4 style="color:var(--text); margin: 14px 0 8px; font-weight:700;">📊 毎月見る数字</h4>'
        '<ul style="padding-left: 0;">'
        '<li><strong>投資意思決定サマリー</strong>: 全保有銘柄の前日比・平均取得単価比・Tierの到達状況（最高値比は銘柄毎の詳細で確認）。</li>'
        '<li><strong>資金投入管理状況</strong>: 原資で積立が続けられる月数と、NISA枠（成長・つみたて）の使用率。</li>'
        '<li><strong>長期ポートフォリオ</strong>: コア（オルカン・S&amp;P500系）とサテライト（それ以外）の割合。</li>'
        '</ul>'
        '<h4 style="color:var(--text); margin: 14px 0 8px; font-weight:700;">⚠️ 注意</h4>'
        '<ul style="padding-left: 0;">'
        '<li>約定履歴CSVは個人の取引明細のため、公開リポジトリに<strong>コミットしない</strong>でください。</li>'
        '<li>土日は自動実行されません。反映が遅れているときは、GitHubの Actions タブで実行結果を確認します。</li>'
        '</ul>'
        '</div>'

        '</div>'
        '</div>'
        '</section>'
    )


# ------------------------------------------------------------------
# HTML レンダリング
# ------------------------------------------------------------------

def _render_html(
    today_str: str,
    banner_html: str,
    summary_html: str,
    cards_html: str,
    market_html: str,
    funding_html: str,
    integrated_html: str,
    ops_html: str,
    chart_data_json: str,
    settings: dict,
    assets: list[dict],
) -> str:
    peak_start_date = settings.get("peak_start_date", "")
    asset_tabs = "".join(
        f'<button class="chart-tab" data-fund="{a["id"]}" data-currency="{a["currency"]}">{a["short_name"]}</button>'
        for a in assets
    )
    script_js = _SCRIPT_JS.replace("__CHART_DATA__", chart_data_json)

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
  border-bottom: 1px solid rgba(255, 255, 255, 0.03); padding: 10px 20px 12px;
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
.pf-name{{ display: flex; flex-direction: column; align-items: flex-start; text-align: left; gap: 2px; }}
.pf-name__title{{ display: flex; align-items: center; gap: 8px; }}
.pf-share{{ display: flex; align-items: center; gap: 10px; }}
.group-row td{{ background: rgba(255,255,255,0.04); color: var(--text); font-weight: 700; font-size: 12px; padding: 10px 12px; border-bottom: none; }}
.group-sub{{ font-size: 11px; font-weight: 400; color: var(--text-mute); margin-left: 6px; }}
.group-row td{{ border-left: 3px solid currentColor; color: #38bdf8; }}
.pf-share .sat-bar{{ margin: 0; width: 90px; height: 8px; flex: none; }}
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
.sat-bar{{ position: relative; height: 12px; background: rgba(255,255,255,0.06); border-radius: 6px; margin: 22px 0 26px; }}
.sat-bar__fill{{ height: 100%; background: linear-gradient(90deg, #3b82f6, #8b5cf6); border-radius: 6px; }}
.sat-bar__target{{ position: absolute; top: -5px; width: 2px; height: 22px; background: var(--yellow); }}
.sat-bar__target-label{{ position: absolute; top: -22px; transform: translateX(-50%); font-size: 10px; color: var(--yellow); white-space: nowrap; }}
.sat-bar__limit{{ position: absolute; top: -5px; width: 2px; height: 22px; background: var(--red); }}
.sat-bar__limit-label{{ position: absolute; top: 18px; transform: translateX(-50%); font-size: 10px; color: var(--red); white-space: nowrap; }}
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
.num{{ text-align: right; font-variant-numeric: tabular-nums; }}
.val-tier{{ white-space: nowrap; }}
.th-short, .t-short, .show-sm{{ display: none; }}
.nm{{ display: flex; flex-direction: column; gap: 2px; }}
.nm-main{{ display: flex; align-items: center; gap: 8px; }}
.nm-name{{ font-weight: 600; }}
.nm-sub{{ display: flex; flex-wrap: wrap; align-items: center; gap: 0 4px; padding-left: 16px; }}
.nm-sub .group-sub{{ font-size: 10px; }}
.nm-sub .inactive-tag, .nm-sub .group-sub{{ margin-left: 0; }}
.summary-table th.num{{ text-align: right; }}
/* 注意バナー */
.alert-banner{{ background: var(--panel-bg); border: 1px solid rgba(255,255,255,0.04); border-radius: 14px; padding: 12px 16px; font-size: 13px; line-height: 1.7; box-shadow: var(--shadow-out); }}
.bn-hit{{ color: #fb923c; font-weight: 700; }}
.bn-ok{{ color: #4ade80; font-weight: 700; }}
.bn-warn{{ color: #f87171; font-weight: 700; }}
.bn-link{{ color: inherit; text-decoration: underline; text-underline-offset: 3px; }}
/* 固定ナビ */
.sec-nav{{ display: flex; gap: 6px; overflow-x: auto; max-width: 900px; margin: 10px auto 0; scrollbar-width: none; }}
.sec-nav::-webkit-scrollbar{{ display: none; }}
.sec-nav a{{ flex: none; padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 600; color: var(--text-mute); background: rgba(255,255,255,0.04); text-decoration: none; }}
.sec-nav a.active{{ color: #60a5fa; background: rgba(59,130,246,0.15); }}
.main > section, .main > div{{ scroll-margin-top: 110px; }}
.asset-acc{{ scroll-margin-top: 110px; }}
/* 銘柄毎の詳細（折りたたみ） */
.acc-head{{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
.acc-head .section-title{{ margin-bottom: 0; }}
.acc-list{{ display: flex; flex-direction: column; gap: 10px; }}
.asset-acc{{ background: var(--panel-bg); box-shadow: var(--shadow-out); border-radius: 14px; border: 1px solid rgba(255,255,255,0.02); border-left: 5px solid var(--fund-color, #3b82f6); overflow: hidden; }}
.asset-sum{{ list-style: none; display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer; }}
.asset-sum::-webkit-details-marker{{ display: none; }}
.as-name-block{{ display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; }}
.as-name{{ font-size: 14px; font-weight: 700; }}
.as-price{{ font-size: 11px; color: var(--text-mute); font-variant-numeric: tabular-nums; }}
.as-dd{{ color: var(--red); font-weight: 700; font-size: 13px; font-variant-numeric: tabular-nums; flex: none; }}
.as-tier{{ font-size: 12px; flex: none; min-width: 64px; text-align: right; }}
.as-chev{{ color: var(--text-mute); font-size: 12px; transition: transform 0.2s; flex: none; }}
.asset-acc[open] .as-chev{{ transform: rotate(180deg); }}
.asset-acc[open] > .asset-sum{{ border-bottom: 1px solid rgba(255,255,255,0.05); }}
.asset-acc .fund-card{{ box-shadow: none; border: none; border-radius: 0; background: transparent; padding: 16px; }}
.asset-acc .fund-card:hover{{ transform: none; box-shadow: none; }}
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
  .market-grid.two{{ grid-template-columns: 1fr 1fr; }}
}}
@media(max-width: 600px){{
  /* 投資意思決定サマリー・長期ポートフォリオ: スマホでも1銘柄1行のコンパクトな表にする */
  .table-wrapper{{ padding: 4px 2px; overflow-x: visible; }}
  .summary-table{{ min-width: 0; font-size: 12px; }}
  .summary-table th{{ padding: 6px 4px; font-size: 10px; letter-spacing: 0; }}
  .summary-table td{{ padding: 8px 4px; font-size: 12px; }}
  .summary-table td:first-child{{ width: 40%; }}
  .th-full, .t-full{{ display: none; }}
  .th-short, .t-short{{ display: inline; }}
  .hide-sm{{ display: none; }}
  .show-sm{{ display: inline; }}
  .summary-table tr.group-row td{{ padding: 8px 8px; font-size: 12px; }}
  .nm-sub{{ padding-left: 14px; flex-wrap: nowrap; white-space: nowrap; }}
  .nm-sub .inactive-tag, .nm-sub .group-sub{{ font-size: 9px; }}
  .nm-sub .role-tag{{ display: none; }}
  .pf-share{{ flex-direction: column; align-items: flex-end; gap: 3px; }}
  .pf-share .sat-bar{{ width: 56px; }}
  .chart-tabs{{ flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }}
  .chart-tab{{ flex: none; }}
  .as-tier{{ min-width: 48px; }}
}}
@media(max-width: 360px){{
  .main{{ padding: 12px 8px; }}
  .section-panel{{ padding: 14px 10px; }}
  .table-wrapper{{ padding: 2px 0; }}
  .summary-table th, .summary-table td{{ padding-left: 2px; padding-right: 2px; }}
  .summary-table td{{ font-size: 11px; }}
  .summary-table td:first-child{{ width: 34%; }}
  .summary-table th{{ font-size: 9px; }}
  .group-row td{{ padding: 8px 6px; }}
  .nm-main{{ gap: 5px; }}
  .nm-sub{{ padding-left: 11px; }}
  .pf-share .sat-bar{{ width: 44px; }}
}}
@media(max-width: 480px){{
  .header{{ padding: 8px 12px; }}
  .header-inner{{ gap: 8px; }}
  .header-title{{ font-size: 16px; }}
  .header-title span{{ display: none; }}
  .header-sub{{ font-size: 10px; line-height: 1.3; }}
  .hs-tag{{ display: none; }}
  .sec-nav{{ margin-top: 6px; }}
  .sec-nav a{{ padding: 4px 10px; }}
  .main > section, .main > div, .asset-acc{{ scroll-margin-top: 92px; }}
  .asset-sum{{ padding: 9px 12px; }}
  .sat-hero{{ padding: 12px 14px; }}
  .sat-hero__label{{ margin-bottom: 2px; }}
  .sat-hero__value{{ font-size: 28px; }}
  .sat-bar{{ margin: 12px 0 10px; }}
  .market-card{{ padding: 12px; }}
  .market-card__value{{ font-size: 20px; }}
  .market-card__note{{ font-size: 10px; }}
  .chart-wrapper{{ height: 240px; padding: 8px; }}
  .tier-progress{{ flex-direction: column; gap: 6px; }}
  .metric-value{{ font-size: 14px; }}
  .alert-banner{{ padding: 10px 12px; font-size: 12px; }}
}}
.val-tier.tier-hit, .status-badge.tier-hit {{ color: #fb923c; font-weight: 700; }}
.val-tier.tier-ok, .status-badge.tier-ok {{ color: #94a3b8; }}
.val-tier.tier-none, .status-badge.tier-none {{ color: #64748b; }}
</style>
</head>
<body>

<header class="header">
  <div class="header-inner">
    <div class="header-title">🛡️ VALOR SHIELD <span style="font-weight:400; font-size:14px; color:var(--text-mute); margin-left:4px;">暴落監視</span></div>
    <div class="header-sub">
      <span>更新: {today_str} JST</span><br>
      <span class="hs-tag" style="color:var(--blue); font-weight:600;">全保有銘柄の下落監視</span>
    </div>
  </div>
  <nav class="sec-nav" id="secNav">
    <a href="#sec-summary">サマリー</a><a href="#sec-detail">詳細</a><a href="#sec-chart">推移</a><a href="#sec-market">市場</a><a href="#sec-funding">資金</a><a href="#sec-portfolio">保有</a>
  </nav>
</header>

<main class="main">

  <!-- ===== 注意バナー ===== -->
  {banner_html}

  <!-- ===== ルールと見方 (アコーディオン) ===== -->
  <section class="guide-section">
    <button class="guide-toggle" id="guideToggle">
      <span>💡 ルールと見方</span>
      <span class="toggle-icon" style="display:inline-block; transition:transform 0.2s;">▼</span>
    </button>
    <div class="guide-content" id="guideContent">
      <div class="guide-grid" style="padding: 16px 0;">
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">📐 数字の見方</h4>
          <ul style="padding-left: 0;">
            <li style="margin-bottom:8px;"><strong>最高値比（下落率）:</strong> {peak_start_date}（資産によっては最初に購入した日）以降の設定来最高値から、現在値が何％下がっているか（例: <code>-15.0%</code>）。銘柄毎の詳細とLINEに表示します（LINEでは「最高比」）。</li>
            <li style="margin-bottom:8px;"><strong>前日比:</strong> 直前の記録（値が変わった前営業日）に対する、現在値の上昇・下落率。</li>
            <li style="margin-bottom:8px;"><strong>平均取得単価比:</strong> 約定履歴から計算した平均取得単価（旧つみたてNISAを除く）に対する、現在値の損益率（LINEでは「平均比」）。米国株はUSD基準で、為替の影響を含みません。</li>
            <li style="margin-bottom:8px;"><strong>Tier:</strong> 最高値比の「下落の目安」（Tier1〜3）。購入の指示や推奨ではありません。しきい値は銘柄ごとに <code>config/settings.json</code> で設定し、未設定の銘柄は「Tier未設定」と表示します。</li>
            <li style="margin-bottom:8px;"><strong>価格のタイミング:</strong> 投資信託の基準価額は前営業日の海外市場終値をもとに決まるため、数字は1営業日遅れで更新されます。</li>
          </ul>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">🔔 LINE通知のルール</h4>
          <ul style="padding-left: 0;">
            <li style="margin-bottom:8px;">下落アラートの対象は積立対象の銘柄（オルカン・S&amp;P500・Tracers・NASDAQ100）。新しいTierに到達したときだけ通知し、下落が続いている間は再通知しません。下落率がそのTierのしきい値の半分より浅くなると解除され、再び下落したときに通知されます。</li>
            <li style="margin-bottom:8px;">LINEの内容は<strong>最高比（最高値比）と平均比（平均取得単価比）だけ</strong>で、プラスは緑・マイナスは赤で表示します。購入額や売買の指示は含みません。毎朝の日次サマリーは、全保有銘柄の最高比と平均比です。</li>
          </ul>
          <h4 style="color:var(--text); margin: 14px 0 8px; font-weight:700;">📌 運用ルール</h4>
          <ul style="padding-left: 0;">
            <li style="margin-bottom:8px;">積立は設定どおり継続します。SOXとFANG+は新規買付を停止し、保有は継続します。</li>
            <li style="margin-bottom:8px;">追加買付（個別株・手動買付）は枠を設けず、<strong>原資から賄います</strong>。現金積立の期間は原資の残額で調整します。</li>
            <li style="margin-bottom:8px;">サテライトは、オルカンとS&amp;P500以外の資産です。</li>
            <li style="margin-bottom:8px;">実際の買付はSBI証券で手動です。このツールは気づきを与えるだけで、自動発注はしません。</li>
          </ul>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 毎月の運用手順（サマリ） ===== -->
  {ops_html}

  <!-- ===== 投資意思決定サマリー ===== -->
  <section class="section-panel" id="sec-summary">
    <div class="section-title">投資意思決定サマリー</div>
    <div class="table-wrapper">
      <table class="summary-table">
        <thead>
          <tr>
            <th>銘柄</th>
            <th class="num">前日比</th>
            <th class="num"><span class="th-full">平均取得単価比</span><span class="th-short">平均比</span></th>
            <th class="num"><span class="th-full">Tier到達</span><span class="th-short">Tier</span></th>
          </tr>
        </thead>
        <tbody>
          {summary_html}
        </tbody>
      </table>
    </div>
  </section>

  <!-- ===== 銘柄毎の詳細（全保有銘柄） ===== -->
  <section id="sec-detail">
    <div class="acc-head">
      <div class="section-title">銘柄毎の詳細</div>
      <button class="chart-tab" id="accToggleAll" type="button">すべて開く</button>
    </div>
    <div class="acc-list">
      {cards_html}
    </div>
  </section>

  <!-- ===== 推移チャート ===== -->
  <section class="section-panel" id="sec-chart">
    <div class="section-title">基準価額の推移とTier閾値</div>
    <div class="chart-tabs" id="chartTabs">
      <button class="chart-tab active" data-fund="all" data-currency="JPY">全銘柄</button>
      {asset_tabs}
    </div>
    <div style="margin:0 0 8px; display:none;" id="currencyBar">
      <button class="chart-tab" id="currencyToggle" type="button">表示通貨: USD（クリックで円換算）</button>
    </div>
    <div class="chart-wrapper">
      <canvas id="mainChart"></canvas>
    </div>
  </section>

  <!-- ===== 市場心理（参考情報） ===== -->
  <section class="section-panel" id="sec-market">
    <div class="section-title">🌡️ 市場心理（参考情報）</div>
    <p class="market-disclaimer">これらは売買の判断基準ではありません。Tierの到達は価格（最高値比）だけで判定されます。迷ったときの参考としてご覧ください。</p>
    <button class="guide-toggle" id="marketGuideToggle">
      <span>💡 3つの指標の見方 (初心者向けガイド)</span>
      <span class="toggle-icon" style="display:inline-block; transition:transform 0.2s;">▼</span>
    </button>
    <div class="guide-content" id="marketGuideContent">
      <div class="guide-grid" style="padding: 16px 0;">
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">😨 VIX指数（恐怖指数）</h4>
          <p>市場参加者の不安・警戒感の大きさを数値化した指標です。数値が高いほど「市場全体が動揺している」状態を表します。半導体やテック株のような銘柄は、市場全体が動揺すると相場全体以上に大きく下がりやすい性質があります。VIXが高いときのTier到達は「全面安に巻き込まれている」可能性、VIXが平常のままのTier到達は「個別要因での下落」の可能性を示唆します。</p>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">💰 米10年国債利回り</h4>
          <p>米国政府が10年間お金を借りる際の金利です。成長株は、将来の利益を現在価値に割り引いて株価が形成されるため、金利が上がると株価が下がりやすい関係が知られています。金利が急上昇しているときのTier到達は、金利要因による調整の可能性を示唆します。</p>
        </div>
        <div class="guide-item">
          <h4 style="color:var(--text); margin-bottom:8px; font-weight:700;">💱 USD/JPY（ドル円）</h4>
          <p>1ドルが何円かを表すレートです。投資信託は為替ヘッジなしのため、基準価額の変動には「米国株そのものの値動き」と「円ドルレートの変動」の両方が混ざっています。円高が進んでいるときのTier到達は、見た目の下落の一部が為替要因である可能性を示唆します。</p>
        </div>
      </div>
    </div>
    <div class="market-grid two">
      {market_html}
    </div>
  </section>

  <!-- ===== 資金投入管理状況 ===== -->
  {funding_html}

  <!-- ===== 長期ポートフォリオ（最下部） ===== -->
  {integrated_html}

</main>

<footer class="footer">
  <p>データ出典：日本経済新聞 電子版（投資信託の基準価額）・Yahoo Finance（米国株） ｜ 本ダッシュボードは投資判断の支援のみを行います。投資は自己責任で行ってください。</p>
</footer>

<script>
{script_js}
</script>
</body>
</html>
"""


_SCRIPT_JS = r"""
const RAW_DATA = __CHART_DATA__;

let chartInstance = null;
let currentFilter = 'all';
let usdMode = true; // 米国株の銘柄タブでは、既定でUSD表示（クリックで円換算に切替）

// アコーディオンの開閉制御（汎用化して複数箇所で使い回す）
function setupAccordion(toggleId, contentId) {
  const toggle = document.getElementById(toggleId);
  const content = document.getElementById(contentId);
  if (!toggle || !content) return;
  const icon = toggle.querySelector('.toggle-icon');
  toggle.addEventListener('click', () => {
    if (content.style.maxHeight && content.style.maxHeight !== '0px') {
      content.style.maxHeight = '0px';
      icon.style.transform = 'rotate(0deg)';
    } else {
      content.style.maxHeight = content.scrollHeight + 'px';
      icon.style.transform = 'rotate(180deg)';
    }
  });
}
setupAccordion('guideToggle', 'guideContent');
setupAccordion('marketGuideToggle', 'marketGuideContent');
setupAccordion('opsToggle', 'opsContent');

function purchaseMarkerColor(category) {
  if (category === 'Tier1') return '#eab308';
  if (category === 'Tier2') return '#f97316';
  if (category === 'Tier3') return '#ef4444';
  return '#3b82f6';
}

// 米国株（USD）は、「全銘柄」では円換算、銘柄タブではUSD（usdModeがfalseなら円換算）で表示する
function showsUsd(ds, filter) {
  return ds.currency === 'USD' && filter !== 'all' && usdMode;
}

function unitOf(ds, filter) {
  return showsUsd(ds, filter) ? 'USD' : '円';
}

function buildDatasets(filter) {
  const priceDatasets = RAW_DATA.datasets
    .filter(ds => filter === 'all' || ds.id === filter)
    .map(ds => {
      const usd = showsUsd(ds, filter);
      return {
        ...ds,
        data: ds.currency === 'USD' && !usd ? ds.data_jpy : ds.data,
        unit: unitOf(ds, filter),
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 6,
        tension: 0.25,
        shadowColor: ds.borderColor,
        shadowBlur: 8,
      };
    });

  // 「全銘柄」表示時はTier閾値線・約定実績マーカーは描画しない（視認性を優先）
  if (filter === 'all') return priceDatasets;
  const selected = RAW_DATA.datasets.find(ds => ds.id === filter);
  // 円換算表示のUSD銘柄は、閾値線・約定マーカー（USD建て）を描画しない
  if (selected && selected.currency === 'USD' && !usdMode) return priceDatasets;

  const extra = [];
  const unit = selected ? unitOf(selected, filter) : '円';

  const tierInfo = RAW_DATA.tier_lines[filter];
  if (tierInfo) {
    tierInfo.tier_values.forEach((val, i) => {
      extra.push({
        label: `Tier${i + 1}閾値 (-${tierInfo.tiers[i]}%)`,
        data: RAW_DATA.labels.map(() => val),
        borderColor: tierInfo.color,
        borderWidth: 1,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
        shadowBlur: 0,
        isTierLine: true,
        unit,
      });
    });
  }

  const markers = (RAW_DATA.purchase_markers && RAW_DATA.purchase_markers[filter]) || [];
  if (markers.length > 0) {
    const arr = new Array(RAW_DATA.labels.length).fill(null);
    const meta = new Array(RAW_DATA.labels.length).fill(null);
    markers.forEach(m => { arr[m.index] = m.price; meta[m.index] = m; });
    extra.push({
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
      unit,
    });
  }

  return [...priceDatasets, ...extra];
}

function formatValue(v, unit) {
  if (v === null || v === undefined) return '-';
  return unit === 'USD'
    ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' USD'
    : v.toLocaleString() + '円';
}

function axisUnit(filter) {
  const selected = RAW_DATA.datasets.find(ds => ds.id === filter);
  return selected ? unitOf(selected, filter) : '円';
}

function renderChart(filter = 'all') {
  currentFilter = filter;
  const ctx = document.getElementById('mainChart').getContext('2d');
  const datasets = buildDatasets(filter);

  // USD銘柄のタブでだけ、通貨の切替ボタンを表示する
  const selected = RAW_DATA.datasets.find(ds => ds.id === filter);
  const bar = document.getElementById('currencyBar');
  const toggle = document.getElementById('currencyToggle');
  const isUsd = !!selected && selected.currency === 'USD';
  bar.style.display = isUsd ? 'block' : 'none';
  toggle.textContent = usdMode ? '表示通貨: USD（クリックで円換算）' : '表示通貨: 円換算（クリックでUSD）';

  if (chartInstance) {
    chartInstance.data.datasets = datasets;
    chartInstance.options.plugins.legend.display = filter === 'all';
    chartInstance.update('active');
    return;
  }

  // カスタムプラグインで線の発光エフェクトを描画
  const shadowPlugin = {
    id: 'shadowPlugin',
    beforeDatasetsDraw(chart, args, options) {
      const { ctx } = chart;
      ctx.save();
      chart.data.datasets.forEach((dataset, index) => {
        const meta = chart.getDatasetMeta(index);
        if (!meta.hidden && dataset.shadowBlur > 0) {
          ctx.shadowColor = dataset.shadowColor;
          ctx.shadowBlur = dataset.shadowBlur;
        }
      });
    },
    afterDatasetsDraw(chart, args, options) {
      chart.ctx.restore();
    }
  };

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: RAW_DATA.labels,
      datasets,
    },
    plugins: [shadowPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500, easing: 'easeOutQuart' },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: filter === 'all',
          position: 'top',
          labels: { color: '#94a3b8', font: { size: 11, family: 'Inter' }, boxWidth: 12, usePointStyle: true }
        },
        tooltip: {
          backgroundColor: 'rgba(11, 13, 20, 0.95)',
          titleColor: '#94a3b8',
          bodyColor: '#f8fafc',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          padding: 10,
          bodyFont: { family: 'Inter' },
          titleFont: { family: 'Inter' },
          filter: (item) => item.raw !== null && item.raw !== undefined,
          callbacks: {
            label: (item) => {
              if (item.dataset.isPurchaseMarker) {
                const m = item.dataset.markerMeta[item.dataIndex];
                if (!m) return null;
                return [
                  `★ 約定日: ${m.date}`,
                  `　約定単価: ${formatValue(m.price, m.currency === 'USD' ? 'USD' : '円')}`,
                  `　区分: ${m.category}`,
                  `　投入金額: ${(m.amount || 0).toLocaleString()}円`,
                ];
              }
              return `  ${item.dataset.label}: ${formatValue(item.raw, item.dataset.unit || '円')}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(255,255,255,0.02)' },
          ticks: { color: '#64748b', font: { size: 9, family: 'Inter' }, maxTicksLimit: 10 },
        },
        y: {
          grid: { color: 'rgba(255,255,255,0.02)' },
          ticks: {
            color: '#64748b',
            font: { size: 9, family: 'Inter' },
            callback: (v) => v !== null ? v.toLocaleString() + (axisUnit(currentFilter) === 'USD' ? ' USD' : '円') : ''
          },
        },
      },
    },
  });
}

document.getElementById('chartTabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.chart-tab');
  if (!btn) return;
  document.querySelectorAll('#chartTabs .chart-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderChart(btn.dataset.fund);
});

document.getElementById('currencyToggle').addEventListener('click', () => {
  usdMode = !usdMode;
  renderChart(currentFilter);
});

renderChart('all');

// ===== 銘柄毎の詳細（折りたたみ）=====
const accToggleAll = document.getElementById('accToggleAll');
if (accToggleAll) {
  accToggleAll.addEventListener('click', () => {
    const items = [...document.querySelectorAll('.asset-acc')];
    const open = items.some(d => !d.open);
    items.forEach(d => { d.open = open; });
    accToggleAll.textContent = open ? 'すべて閉じる' : 'すべて開く';
  });
}
// 注意バナーの銘柄名から、該当の詳細を開いて移動する
function openAssetFromHash() {
  const id = decodeURIComponent(location.hash || '').replace('#', '');
  const el = id ? document.getElementById(id) : null;
  if (el && el.tagName === 'DETAILS') el.open = true;
}
document.querySelectorAll('.bn-link').forEach(a => {
  a.addEventListener('click', () => {
    const el = document.querySelector(a.getAttribute('href'));
    if (el) el.open = true;
  });
});
window.addEventListener('hashchange', openAssetFromHash);
openAssetFromHash();

// ===== 固定ナビ: 表示中のセクションを強調 =====
const navLinks = [...document.querySelectorAll('#secNav a')];
if (navLinks.length && 'IntersectionObserver' in window) {
  const byId = new Map(navLinks.map(a => [a.getAttribute('href').slice(1), a]));
  const io = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (en.isIntersecting) {
        navLinks.forEach(l => l.classList.remove('active'));
        const link = byId.get(en.target.id);
        if (link) link.classList.add('active');
      }
    });
  }, { rootMargin: '-110px 0px -65% 0px' });
  byId.forEach((link, id) => { const el = document.getElementById(id); if (el) io.observe(el); });
}

"""
