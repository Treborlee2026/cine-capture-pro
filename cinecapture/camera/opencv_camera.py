"""Camera adapter built on OpenCV (DirectShow or Media Foundation on Windows).

* Devices are listed by name (via pygrabber on Windows) and opened by index.
* Supported modes are read from DirectShow where possible, otherwise probed.
* MJPEG cameras deliver their original JPEG bytes when the driver allows it,
  so "camera JPEG" capture saves them with no re-encoding.
* The capture thread never sleeps: every frame is read and timestamped the
  moment the driver hands it over.
* If the camera disappears, the adapter keeps trying to reopen it and calls
  ``on_reconnect`` so the locked settings can be re-applied.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..core.frames import Frame, now
from .base import Camera, ControlInfo, DeviceInfo, StoppableThread, VideoMode

API = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "avfoundation": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
}


@dataclass(frozen=True)
class _Ctl:
    key: str
    name: str
    prop: int
    auto_prop: Optional[int]
    lo: float
    hi: float
    step: float
    unit: str = ""
    note: str = ""


# Typical UVC ranges. Real cameras differ; the UI lets the value go outside the
# slider range and the driver's own dialog is always available.
CONTROLS: List[_Ctl] = [
    _Ctl("exposure", "Exposure", cv2.CAP_PROP_EXPOSURE, cv2.CAP_PROP_AUTO_EXPOSURE,
         -13, 0, 1, "log2 s", "-6 = 1/64 s, -7 = 1/128 s. Shorter exposure = less blur."),
    _Ctl("gain", "Gain", cv2.CAP_PROP_GAIN, None, 0, 100, 1, "",
         "Keep as low as possible: gain adds noise."),
    _Ctl("white_balance", "White balance", cv2.CAP_PROP_WB_TEMPERATURE, cv2.CAP_PROP_AUTO_WB,
         2800, 6500, 10, "K"),
    _Ctl("brightness", "Brightness", cv2.CAP_PROP_BRIGHTNESS, None, -64, 64, 1),
    _Ctl("contrast", "Contrast", cv2.CAP_PROP_CONTRAST, None, 0, 100, 1),
    _Ctl("saturation", "Saturation", cv2.CAP_PROP_SATURATION, None, 0, 100, 1),
    _Ctl("gamma", "Gamma", cv2.CAP_PROP_GAMMA, None, 72, 500, 1),
    _Ctl("sharpness", "Sharpness", cv2.CAP_PROP_SHARPNESS, None, 0, 100, 1, "",
         "Set to 0 or low: camera sharpening creates halos on film grain."),
    _Ctl("focus", "Focus", cv2.CAP_PROP_FOCUS, cv2.CAP_PROP_AUTOFOCUS, 0, 1023, 1, "",
         "Only on cameras with motorised focus."),
]

COMMON_SIZES = [(3840, 2160), (2592, 1944), (2560, 1440), (1920, 1080), (1600, 1200),
                (1280, 960), (1280, 720), (1024, 768), (800, 600), (640, 480)]


def _fourcc_str(v: float) -> str:
    v = int(v)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00")


def _windows_device_names() -> List[str]:
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore
        return list(FilterGraph().get_input_devices())
    except Exception:  # noqa: BLE001
        return []


def _windows_device_formats(index: int) -> List[VideoMode]:
    """Formats reported by DirectShow for one device (needs pygrabber)."""
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore
        g = FilterGraph()
        g.add_video_input_device(index)
        dev = g.get_input_device()
        fmts = dev.get_formats()
    except Exception:  # noqa: BLE001
        return []
    out = set()
    for f in fmts:
        try:
            sub = str(f.get("media_type_str", "")).upper()
            fourcc = "MJPG" if "MJPG" in sub or "MJPEG" in sub else sub[:4] or "?"
            fps = float(f.get("max_framerate") or f.get("min_framerate") or 30)
            out.add(VideoMode(int(f["width"]), int(f["height"]), round(fps, 2), fourcc))
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda m: (-m.width * m.height, -m.fps, m.fourcc))


class OpenCVCamera(Camera):
    def __init__(self, backend: str = "dshow") -> None:
        super().__init__()
        self.backend = backend
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._thread: Optional[StoppableThread] = None
        self._raw_jpeg = False
        self._seq = 0
        self._wanted_mode: Optional[VideoMode] = None
        self._auto_cache: Dict[str, bool] = {}

    # ------------------------------------------------------------- discovery
    def list_devices(self) -> List[DeviceInfo]:
        names = _windows_device_names() if sys.platform == "win32" else []
        if names:
            return [DeviceInfo(i, n, self.backend) for i, n in enumerate(names)]
        found = []
        for i in range(8):
            cap = cv2.VideoCapture(i, API.get(self.backend, cv2.CAP_ANY))
            ok = cap.isOpened()
            cap.release()
            if ok:
                found.append(DeviceInfo(i, f"Camera {i}", self.backend))
        return found

    def list_modes(self) -> List[VideoMode]:
        if self.device is None:
            return []
        if sys.platform == "win32":
            modes = _windows_device_formats(self.device.index)
            if modes:
                return modes
        return self._probe_modes()

    def _probe_modes(self) -> List[VideoMode]:
        """Best effort when the platform cannot report formats: try common sizes."""
        out = []
        with self._lock:
            cap = self._cap
            if cap is None:
                return []
            for fourcc in ("MJPG", "YUY2"):
                for w, h in COMMON_SIZES:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                    aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = cap.get(cv2.CAP_PROP_FPS) or 30
                    got = _fourcc_str(cap.get(cv2.CAP_PROP_FOURCC)) or fourcc
                    if (aw, ah) == (w, h):
                        out.append(VideoMode(w, h, round(float(fps), 2), got))
            if self.mode is not None:
                self._apply_mode(cap, self.mode)
        return sorted(set(out), key=lambda m: (-m.width * m.height, -m.fps, m.fourcc))

    # ------------------------------------------------------------- lifecycle
    def open(self, device: DeviceInfo, mode: Optional[VideoMode] = None) -> VideoMode:
        self.close()
        self.device = device
        self._wanted_mode = mode
        cap = self._open_cap()
        if cap is None:
            raise RuntimeError(f"Could not open {device.name}. Is another program using it?")
        self._cap = cap
        self._thread = StoppableThread(self._run, f"Camera-{device.index}")
        self._thread.start()
        assert self.mode is not None
        return self.mode

    def _open_cap(self) -> Optional[cv2.VideoCapture]:
        assert self.device is not None
        cap = cv2.VideoCapture(self.device.index, API.get(self.backend, cv2.CAP_ANY))
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        mode = self._wanted_mode
        if mode is None:
            # Default: largest MJPEG mode if the camera has one.
            mode = VideoMode(3840, 2160, 30, "MJPG")
        self._apply_mode(cap, mode)
        self._raw_jpeg = self._try_raw_jpeg(cap)
        self.mode = VideoMode(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                              int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                              round(float(cap.get(cv2.CAP_PROP_FPS) or mode.fps), 2),
                              _fourcc_str(cap.get(cv2.CAP_PROP_FOURCC)) or mode.fourcc)
        return cap

    @staticmethod
    def _apply_mode(cap: cv2.VideoCapture, mode: VideoMode) -> None:
        if mode.fourcc and len(mode.fourcc) == 4:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*mode.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, mode.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, mode.height)
        if mode.fps:
            cap.set(cv2.CAP_PROP_FPS, mode.fps)

    @staticmethod
    def _try_raw_jpeg(cap: cv2.VideoCapture) -> bool:
        """Ask the driver for undecoded MJPEG; keep it only if real JPEG data comes back."""
        if _fourcc_str(cap.get(cv2.CAP_PROP_FOURCC)) != "MJPG":
            return False
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        for _ in range(5):
            ok, data = cap.read()
            if ok and data is not None:
                flat = data.reshape(-1) if data.dtype == np.uint8 else None
                if (flat is not None and (data.ndim == 1 or data.shape[0] == 1)
                        and flat.size > 4 and flat[0] == 0xFF and flat[1] == 0xD8):
                    return True
                break
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        return False

    def close(self) -> None:
        if self._thread is not None:
            self._thread.stop_event.set()
            self._thread.join(3.0)
            self._thread = None
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def delivers_jpeg(self) -> bool:
        return self._raw_jpeg

    # -------------------------------------------------------------- capture
    def _run(self) -> None:
        assert self._thread is not None
        stop = self._thread.stop_event
        failures = 0
        while not stop.is_set():
            with self._lock:
                cap = self._cap
                if cap is None:
                    ok = False
                else:
                    ok = cap.grab()
                t = now()
                data = cap.retrieve()[1] if (ok and cap is not None) else None
            if not ok or data is None:
                failures += 1
                if failures >= 20:
                    self._handle_disconnect(stop)
                    failures = 0
                else:
                    time.sleep(0.01)
                continue
            failures = 0
            self._seq += 1
            frame = Frame(seq=self._seq, timestamp=t)
            if self._raw_jpeg and data.dtype == np.uint8 and (data.ndim == 1 or data.shape[0] == 1):
                frame.jpeg = data.reshape(-1).tobytes()
                frame.width, frame.height = self.mode.width, self.mode.height  # type: ignore[union-attr]
            else:
                frame.image = data
                frame.height, frame.width = data.shape[:2]
            if self.on_frame is not None:
                try:
                    self.on_frame(frame)
                except Exception:  # noqa: BLE001 - never let a consumer kill the camera
                    pass

    def _handle_disconnect(self, stop: threading.Event) -> None:
        if self.on_disconnect:
            self.on_disconnect()
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        while not stop.is_set():
            cap = self._open_cap()
            if cap is not None:
                with self._lock:
                    self._cap = cap
                if self.on_reconnect:
                    self.on_reconnect()
                return
            stop.wait(1.0)

    # ------------------------------------------------------------- controls
    def controls(self) -> List[ControlInfo]:
        out = []
        with self._lock:
            cap = self._cap
            if cap is None:
                return []
            for c in CONTROLS:
                v = cap.get(c.prop)
                if v is None or v == -1 and c.key != "exposure":
                    continue
                if c.key == "focus" and cap.get(cv2.CAP_PROP_AUTOFOCUS) == -1 and v in (0, -1):
                    continue
                auto = None
                if c.auto_prop is not None:
                    a = cap.get(c.auto_prop)
                    auto = self._auto_cache.get(c.key, a not in (0, 0.25, -1))
                lo, hi = min(c.lo, v), max(c.hi, v)
                out.append(ControlInfo(c.key, c.name, lo, hi, c.step, v, float(v),
                                       c.auto_prop is not None, bool(auto), c.unit, c.note))
        return out

    def _ctl(self, key: str) -> _Ctl:
        for c in CONTROLS:
            if c.key == key:
                return c
        raise KeyError(key)

    def set_control(self, key: str, value: float) -> None:
        c = self._ctl(key)
        with self._lock:
            if self._cap is None:
                raise RuntimeError("camera not open")
            if not self._cap.set(c.prop, float(value)):
                raise RuntimeError(f"the camera did not accept {c.name} = {value:g}")

    def get_control(self, key: str) -> Optional[float]:
        c = self._ctl(key)
        with self._lock:
            if self._cap is None:
                return None
            v = self._cap.get(c.prop)
        return None if v is None else float(v)

    def set_auto(self, key: str, auto: bool) -> None:
        c = self._ctl(key)
        if c.auto_prop is None:
            return
        with self._lock:
            if self._cap is None:
                return
            if c.auto_prop == cv2.CAP_PROP_AUTO_EXPOSURE:
                # 0.75 = auto, 0.25 = manual is what DirectShow, Media Foundation and V4L2
                # drivers accept through OpenCV.
                self._cap.set(c.auto_prop, 0.75 if auto else 0.25)
            else:
                self._cap.set(c.auto_prop, 1 if auto else 0)
        self._auto_cache[key] = auto

    def get_auto(self, key: str) -> Optional[bool]:
        c = self._ctl(key)
        if c.auto_prop is None:
            return None
        return self._auto_cache.get(key)

    def show_driver_dialog(self) -> bool:
        with self._lock:
            if self._cap is None or self.backend != "dshow":
                return False
            return bool(self._cap.set(cv2.CAP_PROP_SETTINGS, 1))
