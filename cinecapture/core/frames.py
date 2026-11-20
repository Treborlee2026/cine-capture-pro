"""Frame and trigger records passed between the camera, trigger and capture code."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


def now() -> float:
    """Monotonic high-resolution clock shared by cameras and triggers (seconds)."""
    return time.perf_counter()


@dataclass
class Frame:
    """One frame delivered by a camera.

    Exactly one of ``image`` or ``jpeg`` is guaranteed; both may be set.
    ``jpeg`` holds the camera's own compressed bytes (MJPEG pass-through),
    which are saved untouched when the output format is "camera JPEG".
    """

    seq: int                          # camera frame counter, increases by 1 per frame
    timestamp: float                  # now() when the frame arrived from the driver
    image: Optional[np.ndarray] = None   # decoded BGR (uint8 or uint16)
    jpeg: Optional[bytes] = None         # original compressed bytes, if available
    width: int = 0
    height: int = 0

    def decoded(self) -> np.ndarray:
        """Return the decoded BGR image, decoding the JPEG on first use."""
        if self.image is None:
            import cv2
            if self.jpeg is None:
                raise ValueError("frame has neither image nor jpeg data")
            arr = np.frombuffer(self.jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("camera JPEG could not be decoded")
            self.image = img
            self.height, self.width = img.shape[:2]
        return self.image

    def preview(self, max_width: int = 1280) -> np.ndarray:
        """A fast, reduced-size BGR image for on-screen preview."""
        import cv2
        if self.image is None and self.jpeg is not None:
            arr = np.frombuffer(self.jpeg, dtype=np.uint8)
            flag = cv2.IMREAD_REDUCED_COLOR_2
            if self.width >= 3000:
                flag = cv2.IMREAD_REDUCED_COLOR_4 if max_width <= 1000 else cv2.IMREAD_REDUCED_COLOR_2
            img = cv2.imdecode(arr, flag)
            if img is not None:
                return img
        img = self.decoded()
        if img.dtype != np.uint8:
            img = (img >> 8).astype(np.uint8)
        h, w = img.shape[:2]
        if w > max_width:
            scale = max_width / w
            img = cv2.resize(img, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return img

    @property
    def nbytes(self) -> int:
        if self.jpeg is not None:
            return len(self.jpeg)
        if self.image is not None:
            return int(self.image.nbytes)
        return 0


@dataclass
class TriggerEvent:
    """One pulse from the projector's frame sensor."""

    index: int            # 1-based trigger count within the session
    timestamp: float      # now() when the pulse reached the computer
    source: str = ""      # e.g. "hid", "keyboard", "serial", "simulated"


@dataclass
class MatchedFrame:
    """A trigger paired with the camera frame chosen for it."""

    trigger: TriggerEvent
    frame: Frame
    latency: float = field(default=0.0)  # frame.timestamp - trigger.timestamp
