"""Trigger from a microcontroller (e.g. Arduino) over a USB serial port.

Protocol: the board sends one byte per frame. Any byte counts; the supplied
sketch (docs/arduino/CineCaptureTrigger.ino) sends the letter 'T'. Boards
with native USB (Leonardo, Pro Micro, Pico) have the lowest latency.
"""

from __future__ import annotations

import time
from typing import Dict, List

from ..core.frames import now
from .base import TriggerDevice, TriggerSource

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # noqa: BLE001
    serial = None
    list_ports = None


class SerialTrigger(TriggerSource):
    kind = "serial"
    label = "Arduino / serial board"

    @classmethod
    def available(cls) -> bool:
        return serial is not None

    @classmethod
    def unavailable_reason(cls) -> str:
        return "" if serial is not None else "The serial library (pyserial) is not installed."

    def list_devices(self) -> List[TriggerDevice]:
        if list_ports is None:
            return []
        out = []
        for p in list_ports.comports():
            desc = p.description or p.device
            arduino = "arduino" in desc.lower() or (p.vid in (0x2341, 0x2A03, 0x1B4F, 0x2E8A))
            out.append(TriggerDevice(p.device, f"{p.device}  {desc}",
                                     "Arduino-type board" if arduino else "", arduino))
        out.sort(key=lambda t: not t.recommended)
        return out

    def select_device(self, device_id: str) -> None:
        self.config["device"] = device_id

    def is_configured(self) -> bool:
        return bool(self.config.get("device"))

    def describe(self) -> str:
        return f"Serial board on {self.config.get('device', '?')}"

    def _open(self):
        return serial.Serial(self.config["device"], int(self.config.get("baud", 115200)),
                             timeout=0.1)

    def learn(self, timeout: float = 15.0) -> Dict:
        if serial is None:
            raise RuntimeError(self.unavailable_reason())
        port = self._open()
        try:
            port.reset_input_buffer()
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if port.read(1):
                    return dict(self.config)
        finally:
            port.close()
        raise TimeoutError("Nothing arrived from the board. Check the port and the sketch, "
                           "then turn the projector by hand once.")

    def _run(self) -> None:
        if serial is None:
            raise RuntimeError(self.unavailable_reason())
        while not self._stop.is_set():
            try:
                port = self._open()
            except Exception as exc:  # noqa: BLE001
                if self.on_error:
                    self.on_error(f"Cannot open {self.config.get('device')}: {exc}")
                self._stop.wait(1.0)
                continue
            try:
                port.reset_input_buffer()
                while not self._stop.is_set():
                    data = port.read(1)
                    t = now()
                    if data:
                        self._fire(t)
                        extra = port.in_waiting
                        if extra:                      # discard newline etc.
                            port.read(extra)
            except Exception as exc:  # noqa: BLE001
                if self.on_error:
                    self.on_error(f"Serial board disconnected ({exc}); reconnecting...")
            finally:
                port.close()
