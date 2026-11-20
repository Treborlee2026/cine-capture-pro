"""App-wide settings (not project data), stored as JSON in the user's AppData folder."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from PySide6.QtCore import QStandardPaths

DEFAULTS: Dict[str, Any] = {
    "camera": {"backend": "", "device_name": "", "mode": None},
    "trigger": {"kind": "hid"},
    "delay_ms": 40.0,
    "debounce_ms": 15.0,
    "file_format": "jpeg",
    "jpeg_quality": 95,
    "buffer_gb": 4.0,
    "end_of_reel": True,
    "end_of_reel_frames": 24,
    "auto_stop_at_end": True,
    "view": {"peaking": False, "zebra": True, "guides": True, "flip_h": False,
             "flip_v": False, "rotate": 0, "histogram": True, "preview_fps": 20,
             "show_captured": False},
    "presets": {},          # name -> {"values": {...}, "gauge": ...}
    "last_project": "",
    "recent_projects": [],
    "first_run_done": False,
}


def settings_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cinecapturepro")
    os.makedirs(base, exist_ok=True)
    return base


class Settings:
    def __init__(self, path: str = "") -> None:
        self.path = path or os.path.join(settings_dir(), "settings.json")
        self.data: Dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                self.data[k].update(v)
            else:
                self.data[k] = v

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def export_to(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)

    def import_from(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            self.data.update(json.load(fh))
        self.save()

    def reset(self) -> None:
        self.data = json.loads(json.dumps(DEFAULTS))
        self.save()
