"""Pair each projector trigger with exactly one camera frame.

Rule: for a trigger at time T and a user-set delay D, the frame kept is the
first camera frame that arrived at or after T + D. If a later trigger's
deadline has also passed by the time that frame arrives, the frame shows the
*later* film frame, so the earlier trigger is reported as dropped.

The matcher is thread-safe: cameras call :meth:`add_frame` from their own
thread and trigger sources call :meth:`add_trigger` from theirs.
"""

from __future__ import annotations

import hashlib
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

from .frames import Frame, MatchedFrame, TriggerEvent


@dataclass
class MatcherSettings:
    delay: float = 0.040          # seconds to wait after a trigger before accepting a frame
    debounce: float = 0.015       # triggers closer together than this are treated as bounce
    max_wait: float = 1.0         # a trigger with no frame after this long is dropped
    detect_duplicates: bool = True


@dataclass
class MatcherStats:
    triggers: int = 0             # accepted triggers
    bounces: int = 0              # triggers ignored as contact bounce
    matched: int = 0
    dropped: int = 0
    duplicates: int = 0


class FrameMatcher:
    def __init__(
        self,
        settings: Optional[MatcherSettings] = None,
        on_match: Optional[Callable[[MatchedFrame], None]] = None,
        on_drop: Optional[Callable[[TriggerEvent, str], None]] = None,
        on_duplicate: Optional[Callable[[MatchedFrame], None]] = None,
    ) -> None:
        self.settings = settings or MatcherSettings()
        self.on_match = on_match
        self.on_drop = on_drop
        self.on_duplicate = on_duplicate
        self.stats = MatcherStats()
        self._pending: Deque[TriggerEvent] = deque()
        self._lock = threading.Lock()
        self._last_trigger_time: Optional[float] = None
        self._last_digest: Optional[bytes] = None
        self._next_index = 1

    # ------------------------------------------------------------------ input
    def add_trigger(self, timestamp: float, source: str = "") -> Optional[TriggerEvent]:
        """Register a trigger pulse. Returns the event, or None if ignored as bounce."""
        with self._lock:
            if (self._last_trigger_time is not None
                    and timestamp - self._last_trigger_time < self.settings.debounce):
                self.stats.bounces += 1
                return None
            self._last_trigger_time = timestamp
            event = TriggerEvent(index=self._next_index, timestamp=timestamp, source=source)
            self._next_index += 1
            self.stats.triggers += 1
            self._pending.append(event)
            return event

    def add_frame(self, frame: Frame) -> Optional[MatchedFrame]:
        """Offer a camera frame. Returns the match it completed, if any."""
        drops: List[TriggerEvent] = []
        timeouts: List[TriggerEvent] = []
        match: Optional[MatchedFrame] = None
        duplicate = False
        with self._lock:
            delay = self.settings.delay
            # Triggers whose window has opened by the time this frame arrived.
            eligible = 0
            for trig in self._pending:
                if frame.timestamp >= trig.timestamp + delay:
                    eligible += 1
                else:
                    break
            if eligible:
                for _ in range(eligible - 1):
                    drops.append(self._pending.popleft())
                trig = self._pending.popleft()
                match = MatchedFrame(trigger=trig, frame=frame,
                                     latency=frame.timestamp - trig.timestamp)
                self.stats.matched += 1
                self.stats.dropped += len(drops)
                if self.settings.detect_duplicates:
                    digest = _frame_digest(frame)
                    if digest is not None and digest == self._last_digest:
                        duplicate = True
                        self.stats.duplicates += 1
                    self._last_digest = digest
            timeouts = self._expire(frame.timestamp)
        for trig in drops:
            self._emit_drop(trig, "next trigger arrived before a fresh camera frame")
        for trig in timeouts:
            self._emit_drop(trig, "no camera frame arrived (timeout)")
        if match is not None:
            if self.on_match:
                self.on_match(match)
            if duplicate and self.on_duplicate:
                self.on_duplicate(match)
        return match

    def check_timeouts(self, current_time: float) -> None:
        """Call periodically so triggers are dropped even if the camera has stopped."""
        with self._lock:
            timeouts = self._expire(current_time)
        for trig in timeouts:
            self._emit_drop(trig, "no camera frame arrived (timeout)")

    def flush(self) -> None:
        """Drop every trigger still waiting (used when capture stops)."""
        with self._lock:
            left = list(self._pending)
            self._pending.clear()
            self.stats.dropped += len(left)
        for trig in left:
            self._emit_drop(trig, "capture stopped before a frame arrived")

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    # --------------------------------------------------------------- internal
    def _expire(self, current_time: float) -> List[TriggerEvent]:
        out: List[TriggerEvent] = []
        limit = self.settings.max_wait + self.settings.delay
        while self._pending and current_time - self._pending[0].timestamp > limit:
            out.append(self._pending.popleft())
        self.stats.dropped += len(out)
        return out

    def _emit_drop(self, trig: TriggerEvent, reason: str) -> None:
        if self.on_drop:
            self.on_drop(trig, reason)


def _frame_digest(frame: Frame) -> Optional[bytes]:
    """A cheap fingerprint to spot a camera re-sending an identical buffer."""
    h = hashlib.blake2b(digest_size=16)
    if frame.jpeg is not None:
        h.update(frame.jpeg)
    elif frame.image is not None:
        h.update(frame.image[::4, ::4].tobytes())
    else:
        return None
    return h.digest()
