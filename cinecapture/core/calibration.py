"""Trigger-delay calibration.

During a calibration run the app records every trigger time and every camera
frame (time + sharpness score) without saving anything. :func:`suggest_delay`
then replays the same recording with many candidate delays and picks the delay
that gives consistently sharp frames, centred in the safe range so small timing
jitter does not push it into the pull-down.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


@dataclass
class CalibrationRecording:
    triggers: List[float] = field(default_factory=list)              # trigger timestamps
    frames: List[Tuple[float, float]] = field(default_factory=list)  # (timestamp, sharpness)

    def add_trigger(self, t: float) -> None:
        self.triggers.append(t)

    def add_frame(self, t: float, score: float) -> None:
        self.frames.append((t, score))


@dataclass
class DelayScore:
    delay: float
    sharpness: float    # mean sharpness of chosen frames, 0..1 relative to the sharpest frame seen
    drop_rate: float    # fraction of triggers that would get no frame


@dataclass
class CalibrationResult:
    delay: Optional[float]
    scores: List[DelayScore]
    message: str


def evaluate_delay(triggers: Sequence[float], frames: Sequence[Tuple[float, float]],
                   delay: float) -> Tuple[float, float]:
    """Return (mean raw sharpness of the chosen frames, drop rate) for one delay."""
    times = [f[0] for f in frames]
    chosen: List[float] = []
    drops = 0
    # Ignore the last trigger: its window has no upper bound in the recording.
    usable = len(triggers) - 1
    for i in range(usable):
        start = triggers[i] + delay
        end = triggers[i + 1] + delay
        j = bisect.bisect_left(times, start)
        if j < len(times) and times[j] < end:
            chosen.append(frames[j][1])
        else:
            drops += 1
    if usable <= 0:
        return 0.0, 1.0
    mean = sum(chosen) / len(chosen) if chosen else 0.0
    return mean, drops / usable


def suggest_delay(rec: CalibrationRecording, max_delay: float = 0.5,
                  step: float = 0.002, tolerance: float = 0.95) -> CalibrationResult:
    triggers = sorted(rec.triggers)
    frames = sorted(rec.frames)
    if len(triggers) < 6 or len(frames) < 10:
        return CalibrationResult(None, [], "Not enough data yet: run the projector for at "
                                 "least 6 frames during calibration.")
    peak = max(s for _, s in frames) or 1.0
    scores: List[DelayScore] = []
    d = 0.0
    while d <= max_delay + 1e-9:
        mean, drop = evaluate_delay(triggers, frames, d)
        scores.append(DelayScore(round(d, 4), mean / peak, drop))
        d += step
    valid = [s for s in scores if s.drop_rate == 0.0]
    if not valid:
        return CalibrationResult(None, scores, "Every delay misses frames: the projector is "
                                 "running too fast for the camera. Slow the projector down.")
    best = max(s.sharpness for s in valid)
    good = [s.drop_rate == 0.0 and s.sharpness >= best * tolerance for s in scores]
    # Longest run of consecutive good delays; choose its middle.
    run_start, best_run = None, (0, -1)
    for i, ok in enumerate(good + [False]):
        if ok and run_start is None:
            run_start = i
        elif not ok and run_start is not None:
            if i - 1 - run_start > best_run[1] - best_run[0]:
                best_run = (run_start, i - 1)
            run_start = None
    lo, hi = best_run
    chosen = scores[(lo + hi) // 2].delay
    width_ms = (scores[hi].delay - scores[lo].delay) * 1000
    msg = (f"Suggested delay {chosen * 1000:.0f} ms. Frames are sharp for delays from "
           f"{scores[lo].delay * 1000:.0f} to {scores[hi].delay * 1000:.0f} ms "
           f"(a {width_ms:.0f} ms safe window).")
    if width_ms < 20:
        msg += " The safe window is narrow: consider slowing the projector."
    return CalibrationResult(chosen, scores, msg)


@dataclass
class RateCheck:
    ok: bool
    frames_per_trigger: float
    message: str


def check_rate(camera_fps: float, trigger_fps: float, minimum: float = 3.0) -> RateCheck:
    """Warn when the projector runs too fast for the camera to deliver fresh frames."""
    if trigger_fps <= 0 or camera_fps <= 0:
        return RateCheck(True, 0.0, "")
    ratio = camera_fps / trigger_fps
    if ratio < minimum:
        max_speed = camera_fps / minimum
        return RateCheck(False, ratio, f"Projector too fast: {trigger_fps:.1f} fps with a "
                         f"{camera_fps:.0f} fps camera. Keep below about {max_speed:.1f} fps.")
    return RateCheck(True, ratio, "")
