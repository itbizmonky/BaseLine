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
) -> None:
    """
    ダッシュボードHTMLを生成して public/index.html に書き出す。

    Args:
        today_str: 今日の日付文字列 "YYYY-MM-DD"
        navs: {"fang": float|None, ...} 今日のNAV
        peak: {"fang": {"value": float, "date": str}, ...}
        fund_results: 各ファンドの判定結果リスト（judge.py の結果）
        history: history.csv のデータ（辞書リスト）
        period_info: detect_period() の戻り値
        settings: settings.json の内容
        triggered: triggered.json の内容
    """
    PUBLIC_DIR.mkdir(exist_ok=True)

    # グラフ用データを準備
    chart_data = _build_chart_data(history, settings, peak)

    # 各銘柄カードのHTML
    cards_html = _build_fund_cards(fund_results, navs, peak, triggered, period_info, settings)

    # トレンドサマリーHTML
    trend_html = _build_trend_summary(fund_results)

    # 残資金サマリーHTML
    funds_html = _build_funds_summary(triggered, period_info, settings)

    html = _render_html(
        today_str=today_str,
        period_info=period_info,
        cards_html=cards_html,
        trend_html=trend_html,
        funds_html=funds_html,
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
    """Chart.js 用のデータセットを構築する"""
    fund_colors = {f["id"]: f["color"] for f in settings["funds"]}
    fund_names  = {f["id"]: f["short_name"] for f in settings["funds"]}
    fund_tiers  = {f["id"]: f["tiers"] for f in settings["funds"]}

    # 直近180日（最大）分のデータを使用
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

    # 設定来高値ラインと各Tier閾値ラインも生成
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
    phase_key = period_info.get("phase", "phase2")
    if phase_key not in ("phase2", "phase3"):
        phase_key = "phase2"

    for fund in settings["funds"]:
        fid = fund["id"]
        result = result_map.get(fid, {})
        nav = navs.get(fid)
        peak_info = peak.get(fid, {})
        peak_val = peak_info.get("value")
        peak_date = peak_info.get("date", "-")
        tier = result.get("tier", 0)
        drawdown = result.get("drawdown", 0.0)
        tiers = fund["tiers"]
        color = fund["color"]
        fired = triggered.get(fid, [])

        # Tier状態の表示
        tier_status, tier_class = _tier_status(tier, fired, drawdown, tiers)

        # 次のTierまでの距離
        next_tier_text = _next_tier_text(tier, drawdown, tiers)

        # NAV表示
        nav_str = f"{nav:,.0f}円" if nav is not None else "取得失敗"
        peak_str = f"{peak_val:,.0f}円" if peak_val is not None else "未記録"
        drawdown_str = f"▲{drawdown:.2f}%" if nav is not None else "-"

        # Tier閾値バー
        tier_bars = _tier_bars(tier, tiers, color)

        # 投入済み資金
        from judge import calc_remaining_funds
        remaining = calc_remaining_funds(fid, triggered, phase_key, settings)
        invested_str = f"{remaining['invested']:,}円" if remaining['invested'] > 0 else "-"
        remain_str = f"{remaining['remaining']:,}円"

        card = f"""
<div class="fund-card {tier_class}" style="--fund-color: {color}">
  <div class="fund-card__header">
    <div class="fund-card__name-block">
      <span class="fund-tag" style="background:{color}20;color:{color};border-color:{color}40">{fund['short_name']}</span>
      <div class="fund-card__name">{fund['name']}</div>
      <div class="fund-card__company">{fund['company']}</div>
    </div>
    <div class="fund-card__status {tier_class}">
      {tier_status}
    </div>
  </div>

  <div class="fund-card__metrics">
    <div class="metric-item">
      <div class="metric-label">現在値</div>
      <div class="metric-value">{nav_str}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">設定来高値</div>
      <div class="metric-value">{peak_str}</div>
      <div class="metric-sub">{peak_date}</div>
    </div>
    <div class="metric-item metric-item--highlight">
      <div class="metric-label">下落率</div>
      <div class="metric-value drawdown">{drawdown_str}</div>
      <div class="metric-sub">{next_tier_text}</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">投入済 / 残</div>
      <div class="metric-value" style="font-size:14px">{invested_str} / {remain_str}</div>
    </div>
  </div>

  <div class="tier-progress">
    {tier_bars}
  </div>
</div>
"""
        html_parts.append(card)

    return "\n".join(html_parts)


def _tier_status(tier: int, fired: list, drawdown: float, tiers: list) -> tuple[str, str]:
    """Tier状態の表示テキストとCSSクラスを返す"""
    if tier == 0:
        return "🟢 Tier未到達", "tier-none"
    elif tier == 1:
        fired_note = "（発動済）" if 1 in fired else "（新規到達!）"
        return f"🟡 Tier1到達{fired_note}", "tier-1"
    elif tier == 2:
        fired_note = "（発動済）" if 2 in fired else "（新規到達!）"
        return f"🟠 Tier2到達{fired_note}", "tier-2"
    elif tier == 3:
        fired_note = "（発動済）" if 3 in fired else "（新規到達!）"
        return f"🔴 Tier3到達{fired_note}", "tier-3"
    return "🟢 Tier未到達", "tier-none"


def _next_tier_text(tier: int, drawdown: float, tiers: list) -> str:
    """次のTierまでの距離テキストを返す"""
    next_tier_idx = tier  # 次のTierのインデックス（0始まり）
    if next_tier_idx < len(tiers):
        next_threshold = tiers[next_tier_idx]
        gap = next_threshold - drawdown
        return f"Tier{next_tier_idx + 1}まであと{gap:.1f}pt"
    return "Tier3超過済"


def _tier_bars(tier: int, tiers: list, color: str) -> str:
    """Tier閾値を示すプログレスバーHTMLを生成"""
    bars = []
    labels = ["Tier1", "Tier2", "Tier3"]
    for i, (label, threshold) in enumerate(zip(labels, tiers), start=1):
        active = "active" if tier >= i else ""
        bars.append(
            f'<div class="tier-bar {active}" style="{"background:" + color if active else ""}">'
            f'  <span class="tier-bar__label">{label}</span>'
            f'  <span class="tier-bar__val">▲{threshold}%</span>'
            f'</div>'
        )
    return "\n".join(bars)


# ------------------------------------------------------------------
# トレンドサマリー HTML
# ------------------------------------------------------------------

def _build_trend_summary(fund_results: list[dict]) -> str:
    items = []
    for r in fund_results:
        t5 = r.get("trend_5d", "→")
        t20 = r.get("trend_20d", "→")
        items.append(
            f'<div class="trend-item">'
            f'  <span class="trend-name">{r["short_name"]}</span>'
            f'  <span class="trend-val">5日: {t5}</span>'
            f'  <span class="trend-val">20日: {t20}</span>'
            f'</div>'
        )
    return "\n".join(items)


# ------------------------------------------------------------------
# 残資金サマリー HTML
# ------------------------------------------------------------------

def _build_funds_summary(triggered: dict, period_info: dict, settings: dict) -> str:
    phase_key = period_info.get("phase", "phase2")
    if phase_key not in ("phase2", "phase3"):
        phase_key = "phase2"

    from judge import calc_remaining_funds
    rows = []
    for fund in settings["funds"]:
        fid = fund["id"]
        rem = calc_remaining_funds(fid, triggered, phase_key, settings)
        rows.append(
            f'<tr>'
            f'  <td><span style="color:{fund["color"]}">{fund["short_name"]}</span></td>'
            f'  <td>{rem["invested"]:,}円</td>'
            f'  <td>{rem["remaining"]:,}円</td>'
            f'  <td>{rem["total"]:,}円</td>'
            f'</tr>'
        )
    return "\n".join(rows)


# ------------------------------------------------------------------
# HTML レンダリング
# ------------------------------------------------------------------

def _render_html(
    today_str: str,
    period_info: dict,
    cards_html: str,
    trend_html: str,
    funds_html: str,
    chart_data_json: str,
    settings: dict,
) -> str:
    phase_label = period_info.get("label", "-")
    days_remaining = period_info.get("days_remaining", 0)
    end_date = period_info.get("end_date", "")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>暴落監視ダッシュボード</title>
<meta name="description" content="新NISA攻撃フェーズ 4銘柄 暴落対応ルール監視ダッシュボード">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
/* ===== CSS Reset & Variables ===== */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#080c14;--bg-card:#111827;--bg-glass:rgba(17,24,39,.7);
  --border:rgba(255,255,255,.08);--border-active:rgba(255,255,255,.15);
  --text:#f1f5f9;--text2:#94a3b8;--text3:#475569;
  --green:#10b981;--yellow:#f59e0b;--orange:#f97316;--red:#ef4444;
  --radius:14px;
}}
html{{scroll-behavior:smooth}}
body{{
  font-family:'Noto Sans JP','Inter',sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  background-image:
    radial-gradient(ellipse at 10% 10%,rgba(59,130,246,.07) 0%,transparent 50%),
    radial-gradient(ellipse at 90% 80%,rgba(139,92,246,.05) 0%,transparent 50%);
}}

/* ===== Header ===== */
.header{{
  background:rgba(8,12,20,.9);backdrop-filter:blur(20px);
  border-bottom:1px solid var(--border);padding:12px 16px;
  position:sticky;top:0;z-index:100;
}}
.header-inner{{max-width:900px;margin:0 auto;}}
.header-title{{font-size:16px;font-weight:700;}}
.header-sub{{font-size:12px;color:var(--text2);margin-top:2px;}}
.header-period{{
  display:inline-flex;align-items:center;gap:6px;
  margin-top:6px;padding:4px 12px;border-radius:999px;
  background:rgba(59,130,246,.1);border:1px solid rgba(59,130,246,.25);
  font-size:12px;color:#60a5fa;
}}
.period-dot{{width:7px;height:7px;border-radius:50%;background:#10b981;
  animation:pulse 2s ease-in-out infinite;}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.5}}}}

/* ===== Main ===== */
.main{{max-width:900px;margin:0 auto;padding:16px;display:flex;flex-direction:column;gap:14px;}}

/* ===== Fund Card ===== */
.fund-card{{
  background:var(--bg-glass);backdrop-filter:blur(20px);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:18px;position:relative;overflow:hidden;
  border-top:3px solid var(--fund-color,#3b82f6);
  transition:transform .2s,box-shadow .2s;
}}
.fund-card:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.3);}}
.fund-card__header{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px;}}
.fund-tag{{
  display:inline-block;padding:2px 10px;border-radius:999px;
  font-size:11px;font-weight:700;border:1px solid;margin-bottom:4px;
}}
.fund-card__name{{font-size:13px;font-weight:600;line-height:1.4;}}
.fund-card__company{{font-size:11px;color:var(--text3);margin-top:2px;}}
.fund-card__status{{
  flex-shrink:0;font-size:13px;font-weight:600;text-align:right;
  white-space:nowrap;padding:6px 10px;border-radius:8px;
}}
.tier-none{{background:rgba(16,185,129,.1);color:var(--green);}}
.tier-1{{background:rgba(245,158,11,.15);color:var(--yellow);}}
.tier-2{{background:rgba(249,115,22,.15);color:var(--orange);}}
.tier-3{{background:rgba(239,68,68,.15);color:var(--red);animation:blink 1.5s ease-in-out infinite;}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.6}}}}

/* ===== Metrics ===== */
.fund-card__metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;}}
.metric-item{{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:8px;padding:10px;}}
.metric-item--highlight{{border-color:rgba(239,68,68,.3);}}
.metric-label{{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}}
.metric-value{{font-size:18px;font-weight:700;font-family:'Inter',monospace;}}
.metric-value.drawdown{{color:var(--red);}}
.metric-sub{{font-size:10px;color:var(--text3);margin-top:3px;}}

/* ===== Tier Progress ===== */
.tier-progress{{display:flex;gap:6px;}}
.tier-bar{{
  flex:1;padding:5px 8px;border-radius:6px;
  background:rgba(255,255,255,.04);border:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center;
  font-size:10px;color:var(--text3);transition:all .2s;
}}
.tier-bar.active{{color:#fff;font-weight:700;border-color:transparent;}}
.tier-bar__label{{font-weight:600;}}
.tier-bar__val{{opacity:.8;}}

/* ===== Section Title ===== */
.section-title{{
  font-size:14px;font-weight:700;color:var(--text);
  display:flex;align-items:center;gap:8px;margin-bottom:12px;
}}

/* ===== Trend ===== */
.trend-section{{background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius);padding:16px;}}
.trend-grid{{display:flex;flex-wrap:wrap;gap:8px;}}
.trend-item{{
  display:flex;align-items:center;gap:8px;
  background:rgba(255,255,255,.03);border:1px solid var(--border);
  border-radius:8px;padding:8px 12px;flex:1;min-width:160px;
}}
.trend-name{{font-size:12px;font-weight:600;color:var(--text2);flex:1;}}
.trend-val{{font-size:14px;font-weight:700;font-family:'Inter',monospace;}}

/* ===== Funds Table ===== */
.funds-section{{background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius);padding:16px;}}
.funds-table{{width:100%;border-collapse:collapse;font-size:13px;}}
.funds-table th{{
  text-align:left;padding:6px 10px;color:var(--text3);
  font-size:11px;font-weight:600;text-transform:uppercase;
  border-bottom:1px solid var(--border);
}}
.funds-table td{{padding:10px;border-bottom:1px solid rgba(255,255,255,.04);}}
.funds-table td:not(:first-child){{text-align:right;font-family:'Inter',monospace;}}

/* ===== Chart ===== */
.chart-section{{background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius);padding:16px;}}
.chart-tabs{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;}}
.chart-tab{{
  padding:5px 12px;border-radius:6px;border:1px solid var(--border);
  background:transparent;color:var(--text2);font-size:12px;font-family:inherit;
  cursor:pointer;transition:all .15s;
}}
.chart-tab.active{{background:rgba(59,130,246,.15);border-color:rgba(59,130,246,.4);color:#60a5fa;}}
.chart-wrapper{{position:relative;height:260px;}}

/* ===== Footer ===== */
.footer{{text-align:center;padding:20px;font-size:11px;color:var(--text3);border-top:1px solid var(--border);}}

/* ===== Responsive ===== */
@media(max-width:500px){{
  .fund-card__metrics{{grid-template-columns:1fr 1fr;}}
  .metric-value{{font-size:15px;}}
  .trend-item{{min-width:100%;}}
}}
</style>
</head>
<body>

<header class="header">
  <div class="header-inner">
    <div class="header-title">📊 暴落監視ダッシュボード</div>
    <div class="header-sub">最終更新：{today_str} 07:00 JST &nbsp;｜&nbsp; 新NISA攻撃フェーズ 自動監視</div>
    <div class="header-period">
      <span class="period-dot"></span>
      現在期間：{phase_label}（〜{end_date}・残り{days_remaining}日）
    </div>
  </div>
</header>

<main class="main">

  <!-- ===== 銘柄カード ===== -->
  {cards_html}

  <!-- ===== 推移チャート ===== -->
  <section class="chart-section">
    <div class="section-title">
      📈 基準価額推移チャート
    </div>
    <div class="chart-tabs" id="chartTabs">
      <button class="chart-tab active" data-fund="all">全銘柄</button>
      <button class="chart-tab" data-fund="fang">FANG+</button>
      <button class="chart-tab" data-fund="sox">SOX</button>
      <button class="chart-tab" data-fund="sp500">S&P500</button>
      <button class="chart-tab" data-fund="orkan">オルカン</button>
    </div>
    <div class="chart-wrapper">
      <canvas id="mainChart"></canvas>
    </div>
  </section>

  <!-- ===== トレンドサマリー ===== -->
  <section class="trend-section">
    <div class="section-title">📊 直近トレンド</div>
    <div class="trend-grid">
      {trend_html}
    </div>
  </section>

  <!-- ===== 残資金サマリー ===== -->
  <section class="funds-section">
    <div class="section-title">💰 資金状況（{phase_label}）</div>
    <table class="funds-table">
      <thead>
        <tr>
          <th>銘柄</th>
          <th>投入済</th>
          <th>残り</th>
          <th>原資合計</th>
        </tr>
      </thead>
      <tbody>
        {funds_html}
      </tbody>
    </table>
    <p style="font-size:11px;color:#475569;margin-top:10px">
      ※ 原資金額は config/settings.json で設定してください
    </p>
  </section>

</main>

<footer class="footer">
  <p>データ出典：各運用会社公式サイト / Yahoo!ファイナンス ｜ 投資判断は自己責任でお願いします</p>
</footer>

<script>
// ===== チャート描画 =====
const RAW_DATA = {chart_data_json};

const COLORS = {{
  fang:  '#f97316',
  sox:   '#a855f7',
  sp500: '#3b82f6',
  orkan: '#10b981',
}};

let chartInstance = null;

function buildDatasets(fundFilter) {{
  return RAW_DATA.datasets
    .filter(ds => fundFilter === 'all' || ds.id === fundFilter)
    .map(ds => ({{
      ...ds,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 5,
      tension: 0.3,
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

  chartInstance = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: RAW_DATA.labels,
      datasets,
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 400 }},
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: fundFilter === 'all', position: 'top',
          labels: {{ color: '#94a3b8', font: {{ size: 11 }}, boxWidth: 14 }} }},
        tooltip: {{
          backgroundColor: 'rgba(13,18,32,.95)',
          titleColor: '#94a3b8', bodyColor: '#f1f5f9',
          borderColor: 'rgba(255,255,255,.1)', borderWidth: 1,
          callbacks: {{
            label: (item) => `  ${{item.dataset.label}}: ${{item.raw !== null ? item.raw.toLocaleString() + '円' : '-'}}`,
          }},
        }},
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(255,255,255,.04)' }},
          ticks: {{ color: '#475569', font: {{ size: 10 }}, maxTicksLimit: 8 }},
        }},
        y: {{
          grid: {{ color: 'rgba(255,255,255,.04)' }},
          ticks: {{ color: '#475569', font: {{ size: 10 }},
            callback: (v) => v !== null ? v.toLocaleString() + '円' : '' }},
        }},
      }},
    }},
  }});
}}

// タブ切り替え
document.getElementById('chartTabs').addEventListener('click', (e) => {{
  const btn = e.target.closest('.chart-tab');
  if (!btn) return;
  document.querySelectorAll('.chart-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderChart(btn.dataset.fund);
}});

// 初期描画
renderChart('all');
</script>
</body>
</html>
"""
