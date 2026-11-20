from __future__ import annotations

import json
import os
from pathlib import Path

MODEL = "claude-opus-5-5"
CONFIG_PATH = Path(os.environ.get("NEGOTIATOR_CONFIG", ".negotiator.json"))


def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
