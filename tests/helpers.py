"""テスト共通の準備。scripts/ を import できるようにし、設定ファイルを読む。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def load_settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
