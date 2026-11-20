"""Background writer that saves captured frames to disk without blocking capture.

Frames wait in a memory buffer with a byte budget. When the budget is used up
:meth:`FrameWriter.submit` returns False so the session can pause and alarm;
frames are never silently thrown away.
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .frames import Frame

FORMATS = {
    # key: (file extension, description)
    "jpeg": (".jpg", "Camera JPEG as delivered (fastest, no re-encoding)"),
    "tiff8": (".tif", "TIFF 8-bit, lossless"),
    "tiff16": (".tif", "TIFF 16-bit (only when the camera supplies more than 8 bits)"),
    "png8": (".png", "PNG 8-bit, lossless"),
    "png16": (".png", "PNG 16-bit (only when the camera supplies more than 8 bits)"),
}

DEFAULT_BUFFER_BYTES = 4 * 1024 ** 3


def frame_filename(prefix: str, number: int, fmt: str) -> str:
    return f"{prefix}_{number:06d}{FORMATS[fmt][0]}"


@dataclass
class WriterStats:
    queued_frames: int = 0
    queued_bytes: int = 0
    written: int = 0
    bytes_written: int = 0
    errors: int = 0


class FrameWriter:
    def __init__(
        self,
        folder: str,
        fmt: str = "jpeg",
        prefix: str = "frame",
        jpeg_quality: int = 95,
        max_buffer_bytes: int = DEFAULT_BUFFER_BYTES,
        on_written: Optional[Callable[[int, str, int], None]] = None,
        on_error: Optional[Callable[[int, str], None]] = None,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}")
        self.folder = folder
        self.fmt = fmt
        self.prefix = prefix
        self.jpeg_quality = int(jpeg_quality)
        self.max_buffer_bytes = int(max_buffer_bytes)
        self.on_written = on_written
        self.on_error = on_error
        self.on_warning = on_warning
        self.stats = WriterStats()
        self._q: "queue.Queue[Optional[tuple[int, Frame]]]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._warned_8bit = False
        self._warned_reencode = False

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="FrameWriter", daemon=True)
        self._thread.start()

    def submit(self, number: int, frame: Frame) -> bool:
        """Queue a frame for writing. Returns False if the buffer is full."""
        size = max(1, frame.nbytes)
        with self._lock:
            if self.stats.queued_frames and self.stats.queued_bytes + size > self.max_buffer_bytes:
                return False
            self.stats.queued_frames += 1
            self.stats.queued_bytes += size
        self._q.put((number, frame))
        return True

    def stop(self, timeout: Optional[float] = None) -> None:
        """Finish writing everything queued, then stop the thread."""
        if self._thread is None:
            return
        self._q.put(None)
        self._thread.join(timeout)
        self._thread = None

    @property
    def buffer_fraction(self) -> float:
        with self._lock:
            return self.stats.queued_bytes / self.max_buffer_bytes if self.max_buffer_bytes else 0.0

    # --------------------------------------------------------------- internal
    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            number, frame = item
            size = max(1, frame.nbytes)
            try:
                path, written = self._write(number, frame)
                with self._lock:
                    self.stats.written += 1
                    self.stats.bytes_written += written
                if self.on_written:
                    self.on_written(number, path, written)
            except Exception as exc:  # noqa: BLE001 - reported to the user, never swallowed
                with self._lock:
                    self.stats.errors += 1
                if self.on_error:
                    self.on_error(number, f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self.stats.queued_frames -= 1
                    self.stats.queued_bytes -= size

    def _encode(self, frame: Frame) -> bytes:
        import cv2
        fmt = self.fmt
        if fmt == "jpeg":
            if frame.jpeg is not None:
                return frame.jpeg
            if not self._warned_reencode:
                self._warned_reencode = True
                self._warn("The camera is not sending JPEG data, so frames are being "
                           f"encoded as JPEG at quality {self.jpeg_quality}.")
            img = frame.decoded()
            if img.dtype != np.uint8:
                img = (img >> 8).astype(np.uint8)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        else:
            img = frame.decoded()
            want16 = fmt.endswith("16")
            if want16 and img.dtype != np.uint16:
                if not self._warned_8bit:
                    self._warned_8bit = True
                    self._warn("The camera supplies 8-bit images, so frames are saved as "
                               "8-bit. Saving them as 16-bit would double the file size "
                               "without adding any detail.")
            if not want16 and img.dtype != np.uint8:
                img = (img >> 8).astype(np.uint8)
            ext = FORMATS[fmt][0]
            params = [cv2.IMWRITE_PNG_COMPRESSION, 1] if ext == ".png" else []
            ok, buf = cv2.imencode(ext, img, params)
        if not ok:
            raise IOError("image encoding failed")
        return buf.tobytes()

    def _write(self, number: int, frame: Frame) -> tuple[str, int]:
        data = self._encode(frame)
        path = os.path.join(self.folder, frame_filename(self.prefix, number, self.fmt))
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return path, len(data)

    def _warn(self, msg: str) -> None:
        if self.on_warning:
            self.on_warning(msg)
