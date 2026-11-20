"""Background thread that turns camera frames into preview images.

The camera can deliver 30 fps of 4K; the screen only needs ~20 fps of a
reduced image. The worker always takes the newest frame and drops the rest,
so the interface never falls behind or freezes.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from ..core import analysis
from ..core.frames import Frame
from ..core.preview_render import ViewOptions, orient, render


class LatestFrame:
    """Thread-safe single slot holding the newest camera frame."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[Frame] = None
        self._event = threading.Event()

    def put(self, frame: Frame) -> None:
        with self._lock:
            self._frame = frame
        self._event.set()

    def take(self, timeout: float) -> Optional[Frame]:
        if not self._event.wait(timeout):
            return None
        with self._lock:
            f, self._frame = self._frame, None
            self._event.clear()
        return f


class PreviewWorker(QThread):
    preview_ready = Signal(object)      # PreviewResult
    loupe_ready = Signal(object, float)  # crop (BGR), sharpness

    def __init__(self, source: LatestFrame, parent=None) -> None:
        super().__init__(parent)
        self.source = source
        self.options = ViewOptions()
        self.fps = 20.0
        self.loupe_enabled = True
        self.loupe_point = (0.5, 0.5)    # normalised, in the oriented image
        self.loupe_size = (480, 360)
        self.loupe_fps = 6.0
        self.paused = False
        self._stop = False
        self._last_loupe = 0.0

    def stop(self) -> None:
        self._stop = True
        self.wait(2000)

    def run(self) -> None:
        while not self._stop:
            t0 = time.monotonic()
            frame = self.source.take(0.2)
            if frame is None or self.paused:
                continue
            try:
                small = frame.preview(self.options.max_width)
                src_w = frame.width or small.shape[1]
                src_h = frame.height or small.shape[0]
                res = render(small, self.options, (src_w, src_h))
                self.preview_ready.emit(res)
                if self.loupe_enabled and t0 - self._last_loupe >= 1.0 / self.loupe_fps:
                    self._last_loupe = t0
                    crop = self._loupe(frame)
                    if crop is not None:
                        self.loupe_ready.emit(crop, analysis.sharpness(crop, (0, 0, crop.shape[1],
                                                                              crop.shape[0])))
            except Exception:  # noqa: BLE001 - a bad frame must not stop the preview
                pass
            spent = time.monotonic() - t0
            wait = 1.0 / max(1.0, self.fps) - spent
            if wait > 0:
                time.sleep(wait)

    def _loupe(self, frame: Frame) -> Optional[np.ndarray]:
        full = orient(frame.decoded(), self.options)
        if full.dtype != np.uint8:
            full = (full >> 8).astype(np.uint8)
        h, w = full.shape[:2]
        cw, ch = min(self.loupe_size[0], w), min(self.loupe_size[1], h)
        cx = int(self.loupe_point[0] * w)
        cy = int(self.loupe_point[1] * h)
        x0 = max(0, min(w - cw, cx - cw // 2))
        y0 = max(0, min(h - ch, cy - ch // 2))
        crop = full[y0:y0 + ch, x0:x0 + cw].copy()
        if self.options.peaking:
            mask = analysis.focus_peaking_mask(crop)
            crop[mask] = (0, 0, 255)
        return crop
