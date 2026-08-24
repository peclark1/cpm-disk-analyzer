"""Small per-user settings store for the desktop application."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindowState:
    width: int = 1120
    height: int = 720
    maximized: bool = False


def settings_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = Path(configured) if configured else Path.home() / ".config"
    return base / "cpm-disk-analyzer" / "settings.json"


def load_window_state() -> WindowState:
    try:
        payload = json.loads(settings_path().read_text(encoding="utf-8"))
        width = int(payload.get("window_width", 1120))
        height = int(payload.get("window_height", 720))
        maximized = bool(payload.get("window_maximized", False))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return WindowState()

    if not (780 <= width <= 10000 and 520 <= height <= 10000):
        return WindowState()
    return WindowState(width, height, maximized)


def save_window_state(width: int, height: int, maximized: bool) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {
        "window_width": max(780, int(width)),
        "window_height": max(520, int(height)),
        "window_maximized": bool(maximized),
    }
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
