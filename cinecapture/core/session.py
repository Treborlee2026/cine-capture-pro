"""A capture or calibration run: camera frames + triggers in, numbered files out.

The session knows nothing about Qt. The UI subscribes with :meth:`subscribe`
and receives ``(event, data)`` calls from background threads; the UI layer is
responsible for moving them onto its own thread.

Events
------
``state``        str: "idle", "capturing", "paused", "testing", "stopped"
``match``        dict: number, trigger, latency, sharpness, preview (small BGR image)
``drop``         dict: trigger, number, reason
``duplicate``    dict: number
``written``      dict: number, path, size
``write_error``  dict: number, message
``alarm``        str: something the user must act on (also sounds in the UI)
``warning``      str: information
``end_of_reel``  dict: kind ("clear" | "dark" | "flat"), number
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

import numpy as np

from . import analysis
from .calibration import CalibrationRecording
from .capture_log import CaptureLog
from .frames import Frame, MatchedFrame, TriggerEvent, now
from .matcher import FrameMatcher, MatcherSettings
from .writer import DEFAULT_BUFFER_BYTES, FrameWriter

Listener = Callable[[str, Any], None]


@dataclass
class SessionSettings:
    delay: float = 0.040
    debounce: float = 0.015
    max_wait: float = 1.0
    file_format: str = "jpeg"
    prefix: str = "frame"
    jpeg_quality: int = 95
    buffer_bytes: int = DEFAULT_BUFFER_BYTES
    end_of_reel: bool = True
    end_of_reel_frames: int = 24          # consecutive blank frames that mean "reel ended"
    min_frames_before_end: int = 48       # ignore leader at the start of a reel
    analyse_every: int = 1                # sharpness/blank check every n-th matched frame


class RateMeter:
    """Events per second over a sliding window of the last ``size`` events."""

    def __init__(self, size: int = 30) -> None:
        self._t: Deque[float] = deque(maxlen=size)

    def tick(self, t: float) -> None:
        self._t.append(t)

    def rate(self) -> float:
        if len(self._t) < 2:
            return 0.0
        span = self._t[-1] - self._t[0]
        return (len(self._t) - 1) / span if span > 0 else 0.0

    def clear(self) -> None:
        self._t.clear()


@dataclass
class SessionStats:
    triggers: int = 0
    bounces: int = 0
    matched: int = 0
    saved: int = 0
    dropped: int = 0
    duplicates: int = 0
    write_errors: int = 0
    bytes_written: int = 0
    queued_frames: int = 0
    queue_fraction: float = 0.0
    trigger_rate: float = 0.0
    camera_rate: float = 0.0
    mean_latency_ms: float = 0.0
    elapsed: float = 0.0
    first_number: int = 1
    last_number: int = 0
    dropped_numbers: List[int] = field(default_factory=list)
    duplicate_numbers: List[int] = field(default_factory=list)


class CaptureSession:
    def __init__(self, frames_dir: Optional[str], settings: SessionSettings,
                 first_number: int = 1, log_path: Optional[str] = None) -> None:
        self.frames_dir = frames_dir
        self.settings = settings
        self.first_number = first_number
        self.state = "idle"
        self.mode = "capture"
        self._listeners: List[Listener] = []
        self._lock = threading.Lock()
        self._log = CaptureLog(log_path)
        self._matcher = FrameMatcher(
            MatcherSettings(delay=settings.delay, debounce=settings.debounce,
                            max_wait=settings.max_wait),
            on_match=self._on_match, on_drop=self._on_drop, on_duplicate=self._on_duplicate)
        self._writer: Optional[FrameWriter] = None
        self._trigger_rate = RateMeter(10)
        self._camera_rate = RateMeter(30)
        self._latencies: Deque[float] = deque(maxlen=50)
        self._blank_run = 0
        self._start_time = 0.0
        self._stats = SessionStats(first_number=first_number)
        self.recording: Optional[CalibrationRecording] = None
        self.test_strip: Deque[Dict[str, Any]] = deque(maxlen=8)

    # ------------------------------------------------------------- listeners
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _emit(self, event: str, data: Any = None) -> None:
        for fn in list(self._listeners):
            try:
                fn(event, data)
            except Exception:  # noqa: BLE001 - a broken listener must not stop capture
                pass

    def _set_state(self, state: str) -> None:
        self.state = state
        self._log.write("state", now() - self._start_time if self._start_time else 0.0,
                        detail=state)
        self._emit("state", state)

    # --------------------------------------------------------------- control
    def start_capture(self) -> None:
        if self.frames_dir is None:
            raise ValueError("capture needs a frames folder")
        self.mode = "capture"
        self._writer = FrameWriter(
            self.frames_dir, self.settings.file_format, self.settings.prefix,
            self.settings.jpeg_quality, self.settings.buffer_bytes,
            on_written=self._on_written, on_error=self._on_write_error,
            on_warning=lambda m: self._emit("warning", m))
        self._writer.start()
        self._begin("capturing")

    def start_test(self) -> None:
        """Calibration/test mode: match frames and score them, but save nothing."""
        self.mode = "test"
        self.recording = CalibrationRecording()
        self.test_strip.clear()
        self._begin("testing")

    def _begin(self, state: str) -> None:
        self._start_time = now()
        self._trigger_rate.clear()
        self._blank_run = 0
        self._log.write("start", 0.0, detail=f"mode={self.mode} delay_ms="
                        f"{self.settings.delay * 1000:.1f} format={self.settings.file_format}")
        self._set_state(state)

    def pause(self, reason: str = "") -> None:
        if self.state == "capturing":
            self._set_state("paused")
            if reason:
                self._emit("alarm", reason)
                self._log.write("alarm", now() - self._start_time, detail=reason)

    def resume(self) -> None:
        if self.state == "paused":
            self._set_state("capturing")

    def stop(self) -> SessionStats:
        if self.state in ("idle", "stopped"):
            return self.stats()
        self._matcher.flush()
        if self._writer is not None:
            self._writer.stop()
        self._log.write("stop", now() - self._start_time)
        self._log.flush()
        self._set_state("stopped")
        self._log.close()
        return self.stats()

    def set_delay(self, delay: float) -> None:
        self.settings.delay = delay
        self._matcher.settings.delay = delay

    # ----------------------------------------------------------------- input
    def on_camera_frame(self, frame: Frame) -> None:
        """Called by the camera thread for every frame, including during idle."""
        self._camera_rate.tick(frame.timestamp)
        if self.state not in ("capturing", "paused", "testing"):
            return
        if self.mode == "test" and self.recording is not None:
            # Score every frame so the delay sweep can be replayed later.
            try:
                score = analysis.sharpness(frame.preview(640))
            except Exception:  # noqa: BLE001
                score = 0.0
            self.recording.add_frame(frame.timestamp, score)
        self._matcher.add_frame(frame)

    def on_trigger(self, timestamp: float, source: str = "") -> None:
        if self.state not in ("capturing", "paused", "testing"):
            return
        event = self._matcher.add_trigger(timestamp, source)
        if event is None:
            self._log.write("bounce", timestamp - self._start_time)
            return
        self._trigger_rate.tick(timestamp)
        if self.mode == "test" and self.recording is not None:
            self.recording.add_trigger(timestamp)
        self._log.write("trigger", timestamp - self._start_time, trigger=event.index,
                        detail=source)

    def tick(self) -> None:
        """Call a few times a second: drops triggers whose frame never arrived."""
        self._matcher.check_timeouts(now())

    # ------------------------------------------------------------- callbacks
    def number_for(self, trig: TriggerEvent) -> int:
        return self.first_number + trig.index - 1

    def _on_match(self, m: MatchedFrame) -> None:
        number = self.number_for(m.trigger)
        t = m.frame.timestamp - self._start_time
        self._latencies.append(m.latency)
        with self._lock:
            self._stats.matched += 1
            self._stats.last_number = max(self._stats.last_number, number)
        info: Dict[str, Any] = {"number": number, "trigger": m.trigger.index,
                                "latency": m.latency, "sharpness": None, "preview": None}
        analyse = (self.mode == "test" or self.settings.end_of_reel
                   ) and m.trigger.index % max(1, self.settings.analyse_every) == 0
        preview: Optional[np.ndarray] = None
        if analyse:
            try:
                preview = m.frame.preview(640)
                info["sharpness"] = analysis.sharpness(preview)
                info["preview"] = preview
            except Exception:  # noqa: BLE001
                preview = None

        if self.mode == "test":
            self.test_strip.append(info)
            self._log.write("test_match", t, trigger=m.trigger.index, frame_number=number,
                            camera_seq=m.frame.seq, latency=m.latency,
                            detail=f"sharpness={info['sharpness'] or 0:.1f}")
            self._emit("match", info)
            return

        if self.state == "paused":
            self._record_drop(m.trigger, number, "capture paused")
            return
        assert self._writer is not None
        if not self._writer.submit(number, m.frame):
            self._record_drop(m.trigger, number, "write buffer full")
            self.pause("The capture drive cannot keep up and the write buffer is full. "
                       "Stop the projector. Capture has been paused.")
            return
        self._log.write("frame", t, trigger=m.trigger.index, frame_number=number,
                        camera_seq=m.frame.seq, latency=m.latency)
        self._emit("match", info)
        if preview is not None and self.settings.end_of_reel:
            self._check_end_of_reel(preview, number)

    def _check_end_of_reel(self, preview: np.ndarray, number: int) -> None:
        blank, kind = analysis.is_blank(preview)
        self._blank_run = self._blank_run + 1 if blank else 0
        captured = number - self.first_number + 1
        if (blank and self._blank_run == self.settings.end_of_reel_frames
                and captured >= self.settings.min_frames_before_end):
            self._log.write("end_of_reel", now() - self._start_time, frame_number=number,
                            detail=kind)
            self._emit("end_of_reel", {"kind": kind, "number": number})

    def _on_drop(self, trig: TriggerEvent, reason: str) -> None:
        self._record_drop(trig, self.number_for(trig), reason)

    def _record_drop(self, trig: TriggerEvent, number: int, reason: str) -> None:
        with self._lock:
            self._stats.dropped += 1
            self._stats.dropped_numbers.append(number)
        self._log.write("drop", now() - self._start_time, trigger=trig.index,
                        frame_number=number, detail=reason)
        self._emit("drop", {"trigger": trig.index, "number": number, "reason": reason})

    def _on_duplicate(self, m: MatchedFrame) -> None:
        number = self.number_for(m.trigger)
        with self._lock:
            self._stats.duplicates += 1
            self._stats.duplicate_numbers.append(number)
        self._log.write("duplicate", m.frame.timestamp - self._start_time,
                        trigger=m.trigger.index, frame_number=number, camera_seq=m.frame.seq,
                        detail="camera sent an identical image twice")
        self._emit("duplicate", {"number": number})

    def _on_written(self, number: int, path: str, size: int) -> None:
        with self._lock:
            self._stats.saved += 1
            self._stats.bytes_written += size
        self._emit("written", {"number": number, "path": path, "size": size})

    def _on_write_error(self, number: int, message: str) -> None:
        with self._lock:
            self._stats.write_errors += 1
        self._log.write("write_error", now() - self._start_time, frame_number=number,
                        detail=message)
        self._emit("write_error", {"number": number, "message": message})
        self.pause(f"Frame {number} could not be saved: {message}. Capture has been paused.")

    # ----------------------------------------------------------------- stats
    def stats(self) -> SessionStats:
        m = self._matcher.stats
        with self._lock:
            s = SessionStats(**{k: getattr(self._stats, k) for k in SessionStats.__dataclass_fields__})
            s.dropped_numbers = list(self._stats.dropped_numbers)
            s.duplicate_numbers = list(self._stats.duplicate_numbers)
        s.triggers = m.triggers
        s.bounces = m.bounces
        s.trigger_rate = self._trigger_rate.rate()
        s.camera_rate = self._camera_rate.rate()
        s.mean_latency_ms = (sum(self._latencies) / len(self._latencies) * 1000
                             if self._latencies else 0.0)
        s.elapsed = now() - self._start_time if self._start_time else 0.0
        if self._writer is not None:
            ws = self._writer.stats
            s.queued_frames = ws.queued_frames
            s.queue_fraction = self._writer.buffer_fraction
        return s

    @property
    def camera_rate(self) -> float:
        return self._camera_rate.rate()


def wait_until_written(session: CaptureSession, count: int, timeout: float = 10.0) -> bool:
    """Test helper: block until ``count`` frames have been written."""
    end = time.time() + timeout
    while time.time() < end:
        if session.stats().saved >= count:
            return True
        time.sleep(0.01)
    return False
