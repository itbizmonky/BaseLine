"""
assets.py
=========
保有資産を統一して扱うモジュール。

settings.json の資産は2か所に分かれる:
  - funds[]          : 日経電子版の基準価額を取得する投資信託（円建て）
  - positions.items  : Yahoo Finance から株価を取得する米国個別株（USD建て）
監視・通知・ダッシュボード・比率計算は、どちらも同じ形の「資産」として扱う。このモジュールが、
2つのリストを（表示順＝funds[]→米国株の順で）1つのリストにまとめ、共通の既定値を補う。

各資産の項目（共通）:
  id, name, short_name, color, role(core/satellite/separate), alert(bool), tiers(list),
  currency("JPY"/"USD"), kind("fund"/"stock")
"""

KIND_FUND = "fund"
KIND_STOCK = "stock"

ROLE_CORE = "core"            # コア（オルカン・S&P500系）
ROLE_SATELLITE = "satellite"  # サテライト（コア以外）
ROLE_SEPARATE = "separate"    # 別会計（子供用。比率の分母に含めない）


def _normalize(item: dict, kind: str) -> dict:
    asset = dict(item)
    asset["kind"] = kind
    asset.setdefault("currency", "USD" if kind == KIND_STOCK else "JPY")
    asset.setdefault("role", ROLE_SATELLITE)
    asset.setdefault("alert", False)
    asset["tiers"] = list(asset.get("tiers") or [])
    asset.setdefault("short_name", asset.get("name", asset["id"]))
    asset.setdefault("color", "#94a3b8")
    return asset


def fund_assets(settings: dict) -> list[dict]:
    """投資信託（funds[]）の資産リスト。"""
    return [_normalize(f, KIND_FUND) for f in settings.get("funds", [])]


def stock_assets(settings: dict) -> list[dict]:
    """米国個別株（positions.items）の資産リスト。positions.enabled が false なら空。"""
    positions = settings.get("positions", {})
    if not positions.get("enabled", True):
        return []
    return [_normalize(i, KIND_STOCK) for i in positions.get("items", [])]


def all_assets(settings: dict) -> list[dict]:
    """全保有資産（投資信託→米国株の順）。表示順はこの並び。"""
    return fund_assets(settings) + stock_assets(settings)


def assets_by_id(settings: dict) -> dict[str, dict]:
    """資産IDから資産を引く辞書。"""
    return {a["id"]: a for a in all_assets(settings)}
