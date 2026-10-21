"""
Сохранение настроек между запусками: %APPDATA%\\Autoclicker\\settings.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_FOLDER = "Autoclicker"
FILE_NAME = "settings.json"


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_FOLDER / FILE_NAME


def load() -> dict[str, Any]:
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict[str, Any]) -> bool:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
