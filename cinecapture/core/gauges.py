"""Film gauge presets and footage arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Gauge:
    key: str
    name: str
    frames_per_foot: int
    aspect: float               # picture width / height of the camera aperture
    perfs_per_frame: int
    perf_side: str              # "left", "right" or "both" as seen from the camera
    default_fps: float          # typical shooting speed


GAUGES: Dict[str, Gauge] = {
    "8mm": Gauge("8mm", "Standard 8mm", 80, 1.33, 1, "left", 16.0),
    "super8": Gauge("super8", "Super 8", 72, 1.36, 1, "left", 18.0),
    "16mm": Gauge("16mm", "16mm", 40, 1.37, 2, "both", 24.0),
}


def footage(frames: int, gauge: Gauge) -> float:
    """Length of film in feet for a frame count."""
    return frames / gauge.frames_per_foot


def running_time(frames: int, fps: float) -> float:
    """Playing time in seconds at a projection speed."""
    return frames / fps if fps > 0 else 0.0


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
