"""A simulated projector + camera, for demos and tests without hardware.

The model: the projector runs at ``projector_fps``. In each cycle the claw
pulls the film down during the first ``pulldown`` fraction of the cycle; the
hall sensor fires when the claw has withdrawn (end of pull-down) plus a small
trigger latency. The camera free-runs at ``camera_fps`` with a rolling shutter:
an exposure that overlaps a pull-down produces a smeared, shifted image.
Frames reach the computer ``camera_latency`` seconds after readout ends.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np

from ..core.frames import Frame, now
from .base import Camera, ControlInfo, DeviceInfo, StoppableThread, VideoMode


@dataclass
class RigModel:
    projector_fps: float = 4.0
    pulldown: float = 0.30           # fraction of each cycle the film is moving
    trigger_latency: float = 0.004   # sensor to computer
    camera_fps: float = 30.0
    exposure: float = 0.008
    readout: float = 0.025           # rolling-shutter readout time
    camera_latency: float = 0.060    # end of readout to frame arriving in the app
    width: int = 960
    height: int = 720
    film_frames: int = 10_000        # frames of picture before clear leader
    jpeg: bool = True
    jitter: float = 0.003            # random latency jitter (s)
    seed: int = 1

    def film_position(self, t: float) -> Tuple[int, bool]:
        """(film frame index in the gate, moving?) at time t since start."""
        cyc = t * self.projector_fps
        n = math.floor(cyc)
        return n, (cyc - n) < self.pulldown

    def exposure_blurred(self, t0: float) -> Tuple[int, bool]:
        """Film frame and whether the exposure window [t0, t0+exp+readout] saw motion."""
        t1 = t0 + self.exposure + self.readout
        n0, m0 = self.film_position(t0)
        n1, m1 = self.film_position(t1)
        moving = m0 or m1 or n0 != n1
        # If the window spans a whole pull-down, it sees both frames.
        return n1, moving

    def trigger_times(self, duration: float) -> List[float]:
        out, n = [], 0
        while True:
            t = (n + self.pulldown) / self.projector_fps + self.trigger_latency
            if t > duration:
                return out
            out.append(t)
            n += 1


def render_film_frame(model: RigModel, film_index: int, blurred: bool,
                      rng: np.random.Generator) -> np.ndarray:
    """Draw a synthetic film frame with texture, a frame number and a perforation."""
    w, h = model.width, model.height
    if film_index >= model.film_frames:
        img = np.full((h, w, 3), 235, np.uint8)            # clear leader
        noise = rng.integers(-2, 3, size=(h, w, 1), dtype=np.int16)
        return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    local = np.random.default_rng(model.seed * 100_003 + film_index)
    base = local.integers(40, 200, size=(h // 16, w // 16, 3), dtype=np.uint8)
    img = cv2.resize(base, (w, h), interpolation=cv2.INTER_NEAREST)
    grain = rng.integers(-6, 7, size=(h, w, 1), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + grain, 0, 255).astype(np.uint8)
    # perforation on the left
    cv2.rectangle(img, (10, h // 2 - 40), (60, h // 2 + 40), (245, 245, 245), -1)
    cv2.putText(img, f"{film_index + 1}", (w // 3, h // 2 + 30), cv2.FONT_HERSHEY_SIMPLEX,
                3.0, (255, 255, 255), 6, cv2.LINE_AA)
    if blurred:
        k = max(9, (h // 12) | 1)
        kernel = np.zeros((k, 1), np.float32)
        kernel[:, 0] = 1.0 / k
        img = cv2.filter2D(img, -1, kernel)
        img = np.roll(img, h // 10, axis=0)
    return img


def simulate(model: RigModel, duration: float) -> Iterator[Tuple[str, float, Optional[Frame]]]:
    """Virtual-time event stream for tests: ("trigger", t, None) / ("frame", t, Frame)."""
    rng = np.random.default_rng(model.seed)
    events: List[Tuple[float, str, Optional[Frame]]] = []
    for t in model.trigger_times(duration):
        events.append((t, "trigger", None))
    period = 1.0 / model.camera_fps
    k = 0
    while True:
        t0 = k * period
        arrive = t0 + model.exposure + model.readout + model.camera_latency
        arrive += float(rng.uniform(0, model.jitter))
        if arrive > duration:
            break
        film, blurred = model.exposure_blurred(t0)
        img = render_film_frame(model, film, blurred, rng)
        frame = Frame(seq=k, timestamp=arrive, width=model.width, height=model.height)
        if model.jpeg:
            frame.jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        else:
            frame.image = img
        frame.sim_film_index = film      # type: ignore[attr-defined]
        frame.sim_blurred = blurred      # type: ignore[attr-defined]
        events.append((arrive, "frame", frame))
        k += 1
    events.sort(key=lambda e: e[0])
    for t, kind, frame in events:
        yield kind, t, frame


class SimulatedRig:
    """Shared real-time clock so the simulated camera and trigger stay in step."""

    _instance: Optional["SimulatedRig"] = None

    def __init__(self, model: Optional[RigModel] = None) -> None:
        self.model = model or RigModel()
        self.t0 = now()
        self.running = True

    @classmethod
    def shared(cls) -> "SimulatedRig":
        if cls._instance is None:
            cls._instance = SimulatedRig()
        return cls._instance

    def elapsed(self) -> float:
        return now() - self.t0


class SimulatedCamera(Camera):
    backend = "simulated"

    def __init__(self, rig: Optional[SimulatedRig] = None) -> None:
        super().__init__()
        self.rig = rig or SimulatedRig.shared()
        self._thread: Optional[StoppableThread] = None
        self._controls = {
            "exposure": ControlInfo("exposure", "Exposure", -13, -1, 1, -7, -7, True, False,
                                    "log2 s"),
            "gain": ControlInfo("gain", "Gain", 0, 100, 1, 0, 0),
            "white_balance": ControlInfo("white_balance", "White balance", 2800, 6500, 10,
                                         4600, 4600, True, False, "K"),
            "brightness": ControlInfo("brightness", "Brightness", -64, 64, 1, 0, 0),
            "contrast": ControlInfo("contrast", "Contrast", 0, 100, 1, 50, 50),
        }

    def list_devices(self) -> List[DeviceInfo]:
        return [DeviceInfo(0, "Simulated projector camera", "simulated")]

    def list_modes(self) -> List[VideoMode]:
        m = self.rig.model
        return [VideoMode(m.width, m.height, m.camera_fps, "MJPG" if m.jpeg else "RGB3")]

    def open(self, device: DeviceInfo, mode: Optional[VideoMode] = None) -> VideoMode:
        self.device = device
        self.mode = self.list_modes()[0]
        self._thread = StoppableThread(self._run, "SimulatedCamera")
        self._thread.start()
        return self.mode

    def close(self) -> None:
        if self._thread is not None:
            self._thread.stop_event.set()
            self._thread.join(2)
            self._thread = None

    @property
    def is_open(self) -> bool:
        return self._thread is not None

    @property
    def delivers_jpeg(self) -> bool:
        return self.rig.model.jpeg

    def controls(self) -> List[ControlInfo]:
        return [replace(c) for c in self._controls.values()]

    def set_control(self, key: str, value: float) -> None:
        c = self._controls[key]
        c.value = max(c.minimum, min(c.maximum, float(value)))

    def get_control(self, key: str) -> Optional[float]:
        c = self._controls.get(key)
        return c.value if c else None

    def set_auto(self, key: str, auto: bool) -> None:
        self._controls[key].auto = auto

    def get_auto(self, key: str) -> Optional[bool]:
        c = self._controls.get(key)
        return c.auto if c and c.has_auto else None

    def _run(self) -> None:
        assert self._thread is not None
        stop = self._thread.stop_event
        rng = np.random.default_rng()
        m = self.rig.model
        period = 1.0 / m.camera_fps
        k = int(self.rig.elapsed() / period) + 1
        gain = 1.0
        while not stop.is_set():
            t0 = k * period
            arrive = t0 + m.exposure + m.readout + m.camera_latency + float(rng.uniform(0, m.jitter))
            wait = arrive - self.rig.elapsed()
            if wait > 0:
                time.sleep(wait)
            film, blurred = m.exposure_blurred(t0)
            img = render_film_frame(m, film, blurred, rng)
            ev = self._controls["exposure"].value
            gain = 2.0 ** (ev + 7) * (1 + self._controls["gain"].value / 50)
            if abs(gain - 1.0) > 1e-3:
                img = np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)
            frame = Frame(seq=k, timestamp=self.rig.t0 + arrive, width=m.width, height=m.height)
            if m.jpeg:
                frame.jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
            else:
                frame.image = img
            if self.on_frame:
                self.on_frame(frame)
            k += 1


class SimulatedTrigger:
    """Fires the projector sensor pulses of the shared simulated rig in real time."""

    def __init__(self, rig: Optional[SimulatedRig] = None) -> None:
        self.rig = rig or SimulatedRig.shared()
        self.callback = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, callback) -> None:
        self.callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="SimulatedTrigger", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
            self._thread = None

    def _run(self) -> None:
        m = self.rig.model
        n = int(self.rig.elapsed() * m.projector_fps) + 1
        while not self._stop.is_set():
            t = (n + m.pulldown) / m.projector_fps + m.trigger_latency
            wait = t - self.rig.elapsed()
            if wait > 0:
                if self._stop.wait(wait):
                    break
            if self.callback:
                self.callback(self.rig.t0 + t, "simulated")
            n += 1
