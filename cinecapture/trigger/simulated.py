"""Simulated projector sensor, paired with the simulated camera (demo mode)."""

from __future__ import annotations

from typing import Dict, Optional

from ..camera.simulated import SimulatedRig, SimulatedTrigger
from .base import TriggerSource


class SimulatedTriggerSource(TriggerSource):
    kind = "simulated"
    label = "Simulated projector (demo)"

    def __init__(self, config: Optional[Dict] = None) -> None:
        super().__init__(config)
        self._sim: Optional[SimulatedTrigger] = None

    def describe(self) -> str:
        fps = SimulatedRig.shared().model.projector_fps
        return f"Simulated projector at {fps:g} fps"

    def learn(self, timeout: float = 15.0) -> Dict:
        return dict(self.config)

    def start(self, callback) -> None:  # type: ignore[override]
        self._callback = callback
        self._sim = SimulatedTrigger(SimulatedRig.shared())
        self._sim.start(lambda t, src: self._fire(t))

    def stop(self) -> None:
        if self._sim:
            self._sim.stop()
            self._sim = None
        self._callback = None

    @property
    def running(self) -> bool:
        return self._sim is not None
