"""Plain-text capture log: one CSV line per trigger, frame, drop and alarm."""

from __future__ import annotations

import csv
import io
import threading
from datetime import datetime
from typing import Optional


class CaptureLog:
    HEADER = ["wall_time", "t", "event", "trigger", "frame_number", "camera_seq",
              "latency_ms", "detail"]

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._fh = None
        if path:
            new = True
            try:
                import os
                new = not os.path.exists(path) or os.path.getsize(path) == 0
            except OSError:
                pass
            self._fh = open(path, "a", encoding="utf-8", newline="")
            if new:
                self._fh.write(",".join(self.HEADER) + "\n")

    def write(self, event: str, t: float = 0.0, trigger: Optional[int] = None,
              frame_number: Optional[int] = None, camera_seq: Optional[int] = None,
              latency: Optional[float] = None, detail: str = "") -> None:
        if self._fh is None:
            return
        row = [
            datetime.now().isoformat(timespec="milliseconds"),
            f"{t:.4f}",
            event,
            "" if trigger is None else str(trigger),
            "" if frame_number is None else str(frame_number),
            "" if camera_seq is None else str(camera_seq),
            "" if latency is None else f"{latency * 1000:.1f}",
            detail,
        ]
        buf = io.StringIO()
        csv.writer(buf).writerow(row)
        with self._lock:
            self._fh.write(buf.getvalue())

    def flush(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None
