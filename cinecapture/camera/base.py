"""Camera interface shared by all platforms."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..core.frames import Frame

FrameCallback = Callable[[Frame], None]


@dataclass
class DeviceInfo:
    index: int
    name: str
    backend: str
    path: str = ""          # platform device path, when known

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class VideoMode:
    width: int
    height: int
    fps: float
    fourcc: str             # "MJPG", "YUY2", "NV12" ...

    def __str__(self) -> str:
        return f"{self.width}x{self.height}  {self.fps:g} fps  {self.fourcc}"


@dataclass
class ControlInfo:
    key: str                # "exposure", "gain", "white_balance", "focus", ...
    name: str               # label for the UI
    minimum: float
    maximum: float
    step: float = 1.0
    default: float = 0.0
    value: float = 0.0
    has_auto: bool = False
    auto: bool = False
    unit: str = ""
    note: str = ""          # e.g. how this camera's driver scales the value


class Camera:
    """Base class. Subclasses run their own capture thread and call ``on_frame``."""

    backend = "base"

    def __init__(self) -> None:
        self.on_frame: Optional[FrameCallback] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_reconnect: Optional[Callable[[], None]] = None
        self.device: Optional[DeviceInfo] = None
        self.mode: Optional[VideoMode] = None

    # discovery
    def list_devices(self) -> List[DeviceInfo]:
        raise NotImplementedError

    def list_modes(self) -> List[VideoMode]:
        """Modes supported by the open device (may be a best-effort list)."""
        return []

    # lifecycle
    def open(self, device: DeviceInfo, mode: Optional[VideoMode] = None) -> VideoMode:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        return False

    # controls
    def controls(self) -> List[ControlInfo]:
        return []

    def set_control(self, key: str, value: float) -> None:
        raise NotImplementedError

    def get_control(self, key: str) -> Optional[float]:
        return None

    def set_auto(self, key: str, auto: bool) -> None:
        raise NotImplementedError

    def get_auto(self, key: str) -> Optional[bool]:
        return None

    def show_driver_dialog(self) -> bool:
        """Open the camera driver's own settings window, if the platform has one."""
        return False

    @property
    def delivers_jpeg(self) -> bool:
        """True when frames carry the camera's original JPEG bytes."""
        return False


@dataclass
class Drift:
    key: str
    locked: float
    actual: float


@dataclass
class CameraSettingsLock:
    """Remembers locked camera settings and puts them back when they drift.

    ``values`` maps control key -> value; every locked control also has its
    automatic mode switched off.
    """

    values: Dict[str, float] = field(default_factory=dict)
    locked: bool = False
    tolerance: float = 0.01   # fraction of the control's range

    def lock(self, camera: Camera) -> None:
        self.values = {}
        for c in camera.controls():
            v = camera.get_control(c.key)
            if v is not None:
                self.values[c.key] = float(v)
        self.locked = True

    def unlock(self) -> None:
        self.locked = False

    def apply(self, camera: Camera) -> List[str]:
        """Switch automatic modes off and write every locked value. Returns failures."""
        failed = []
        ranges = {c.key: c for c in camera.controls()}
        for key, value in self.values.items():
            try:
                c = ranges.get(key)
                if c is not None and c.has_auto:
                    camera.set_auto(key, False)
                camera.set_control(key, value)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{key}: {exc}")
        return failed

    def check(self, camera: Camera) -> List[Drift]:
        """Compare the camera's current values with the locked ones."""
        out = []
        ranges = {c.key: c for c in camera.controls()}
        for key, want in self.values.items():
            got = camera.get_control(key)
            if got is None:
                continue
            c = ranges.get(key)
            span = (c.maximum - c.minimum) if c else 1.0
            if abs(float(got) - want) > max(self.tolerance * span, 1e-6):
                out.append(Drift(key, want, float(got)))
            if c is not None and c.has_auto and camera.get_auto(key):
                out.append(Drift(key + " (auto mode)", 0.0, 1.0))
        return out

    def to_dict(self) -> Dict[str, object]:
        return {"locked": self.locked, "values": dict(self.values)}

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "CameraSettingsLock":
        vals = d.get("values", {}) if isinstance(d, dict) else {}
        return cls(values={k: float(v) for k, v in dict(vals).items()},  # type: ignore[arg-type]
                   locked=bool(d.get("locked", False)) if isinstance(d, dict) else False)


class StoppableThread(threading.Thread):
    def __init__(self, target, name: str) -> None:
        super().__init__(target=target, name=name, daemon=True)
        self.stop_event = threading.Event()
