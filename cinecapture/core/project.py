"""Projects and reels on disk.

Layout::

    <Project>/
      project.ccp            project settings and reel list (JSON)
      history/               previous versions of project.ccp (last 20)
      reels/<R01_Title>/
        reel.json            metadata, gauge, stock, camera settings, trigger delay
        capture.log          one line per trigger, frame and alarm
        frames/frame_000001.jpg ...
      exports/
      cache/                 thumbnails and proxies, safe to delete
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

PROJECT_FILE = "project.ccp"
REEL_FILE = "reel.json"
FORMAT_VERSION = 1
HISTORY_KEEP = 20


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def safe_name(text: str) -> str:
    """A folder-safe version of a title."""
    text = re.sub(r"[^\w\- ]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "_", text)[:60] or "Untitled"


@dataclass
class ReelMetadata:
    title: str = ""
    stock: str = ""            # e.g. "Kodachrome 40"
    shoot_date: str = ""
    location: str = ""
    edge_code: str = ""
    notes: str = ""


@dataclass
class Reel:
    id: str
    folder: str                         # absolute path
    gauge: str = "super8"
    negative: bool = False
    metadata: ReelMetadata = field(default_factory=ReelMetadata)
    camera: Dict[str, Any] = field(default_factory=dict)    # locked camera settings
    trigger_delay: float = 0.040
    file_format: str = "jpeg"
    prefix: str = "frame"
    next_frame: int = 1                 # number for the next captured frame
    frames_captured: int = 0
    created: str = field(default_factory=_stamp)

    @property
    def frames_dir(self) -> str:
        return os.path.join(self.folder, "frames")

    @property
    def log_path(self) -> str:
        return os.path.join(self.folder, "capture.log")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("folder")
        d["format_version"] = FORMAT_VERSION
        return d

    def save(self) -> None:
        os.makedirs(self.frames_dir, exist_ok=True)
        _atomic_json(os.path.join(self.folder, REEL_FILE), self.to_dict())

    @classmethod
    def load(cls, folder: str) -> "Reel":
        with open(os.path.join(folder, REEL_FILE), encoding="utf-8") as fh:
            d = json.load(fh)
        d.pop("format_version", None)
        meta = ReelMetadata(**{k: v for k, v in d.pop("metadata", {}).items()
                               if k in ReelMetadata.__dataclass_fields__})
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(folder=folder, metadata=meta, **known)


class Project:
    def __init__(self, folder: str, name: str = "") -> None:
        self.folder = os.path.abspath(folder)
        self.name = name or os.path.basename(self.folder)
        self.created = _stamp()
        self.reels: List[Reel] = []
        self.settings: Dict[str, Any] = {}

    # ------------------------------------------------------------ create/load
    @classmethod
    def create(cls, parent_dir: str, name: str) -> "Project":
        folder = os.path.join(parent_dir, safe_name(name))
        if os.path.exists(os.path.join(folder, PROJECT_FILE)):
            raise FileExistsError(f"A project already exists at {folder}")
        for sub in ("reels", "exports", "cache", "history"):
            os.makedirs(os.path.join(folder, sub), exist_ok=True)
        proj = cls(folder, name)
        proj.save()
        return proj

    @classmethod
    def open(cls, folder_or_file: str) -> "Project":
        folder = folder_or_file
        if os.path.isfile(folder) and folder.endswith(PROJECT_FILE):
            folder = os.path.dirname(folder)
        with open(os.path.join(folder, PROJECT_FILE), encoding="utf-8") as fh:
            d = json.load(fh)
        proj = cls(folder, d.get("name", ""))
        proj.created = d.get("created", proj.created)
        proj.settings = d.get("settings", {})
        for rel in d.get("reels", []):
            path = os.path.join(proj.folder, rel)
            if os.path.exists(os.path.join(path, REEL_FILE)):
                proj.reels.append(Reel.load(path))
        return proj

    # ------------------------------------------------------------------ reels
    def add_reel(self, title: str, gauge: str = "super8", **kwargs: Any) -> Reel:
        number = len(self.reels) + 1
        rid = f"R{number:02d}"
        while any(r.id == rid for r in self.reels):
            number += 1
            rid = f"R{number:02d}"
        folder = os.path.join(self.folder, "reels", f"{rid}_{safe_name(title)}")
        reel = Reel(id=rid, folder=folder, gauge=gauge,
                    metadata=ReelMetadata(title=title), **kwargs)
        reel.save()
        self.reels.append(reel)
        self.save()
        return reel

    def reel(self, rid: str) -> Optional[Reel]:
        return next((r for r in self.reels if r.id == rid), None)

    # ------------------------------------------------------------------- save
    def save(self) -> None:
        path = os.path.join(self.folder, PROJECT_FILE)
        if os.path.exists(path):
            hist = os.path.join(self.folder, "history")
            os.makedirs(hist, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            shutil.copy2(path, os.path.join(hist, f"project_{stamp}.ccp"))
            old = sorted(os.listdir(hist))
            for name in old[:-HISTORY_KEEP]:
                os.remove(os.path.join(hist, name))
        data = {
            "format_version": FORMAT_VERSION,
            "app": "Cine Capture Pro",
            "name": self.name,
            "created": self.created,
            "modified": _stamp(),
            "settings": self.settings,
            "reels": [os.path.relpath(r.folder, self.folder).replace("\\", "/")
                      for r in self.reels],
        }
        _atomic_json(path, data)
