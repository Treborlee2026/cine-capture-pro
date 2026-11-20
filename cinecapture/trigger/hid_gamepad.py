"""Trigger from a USB HID gamepad button (recommended: u-HID Nano in gamepad mode).

The device is read directly with the HID API, so:
  * capture works whatever window has focus, and
  * no key press ever reaches another program.

The button is identified by "learning": the user presses it once and the app
records which bit of the HID report changes.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from ..core.frames import now
from .base import TriggerDevice, TriggerSource

ULTIMARC_VENDOR_ID = 0xD209   # Ultimarc (u-HID, I-PAC, ...)

try:  # cython-hidapi ("pip install hidapi") or the ctypes "hid" package
    import hid as _hid  # type: ignore
except Exception:  # noqa: BLE001
    _hid = None


class _Dev:
    """Small wrapper over the two common Python HID libraries."""

    def __init__(self, path: bytes) -> None:
        if hasattr(_hid, "device"):          # cython-hidapi
            self._d = _hid.device()
            self._d.open_path(path)
            self._style = "cython"
        else:                                 # "hid" ctypes package
            self._d = _hid.Device(path=path)
            self._style = "ctypes"

    def read(self, size: int = 64, timeout_ms: int = 100) -> List[int]:
        data = self._d.read(size, timeout_ms)
        return list(data) if data else []

    def close(self) -> None:
        try:
            self._d.close()
        except Exception:  # noqa: BLE001
            pass


def _enumerate() -> List[dict]:
    if _hid is None:
        return []
    return list(_hid.enumerate(0, 0))


def _path_str(path) -> str:
    return path.decode("latin-1") if isinstance(path, (bytes, bytearray)) else str(path)


def _is_game_controller(d: dict) -> bool:
    return d.get("usage_page") == 1 and d.get("usage") in (4, 5)


class HidGamepadTrigger(TriggerSource):
    kind = "hid"
    label = "Gamepad button (HID)"

    @classmethod
    def available(cls) -> bool:
        return _hid is not None

    @classmethod
    def unavailable_reason(cls) -> str:
        return "" if _hid is not None else "The HID library (hidapi) is not installed."

    def list_devices(self) -> List[TriggerDevice]:
        out, seen = [], set()
        for d in _enumerate():
            if not _is_game_controller(d):
                continue
            key = _path_str(d["path"])
            if key in seen:
                continue
            seen.add(key)
            name = (d.get("product_string") or "HID game controller").strip()
            maker = (d.get("manufacturer_string") or "").strip()
            label = f"{name} ({maker})" if maker and maker not in name else name
            label += f"  [{d['vendor_id']:04X}:{d['product_id']:04X}]"
            ultimarc = d.get("vendor_id") == ULTIMARC_VENDOR_ID
            out.append(TriggerDevice(key, label, "Ultimarc device" if ultimarc else "",
                                     recommended=ultimarc))
        out.sort(key=lambda t: not t.recommended)
        return out

    def _resolve_path(self) -> bytes:
        """Find the configured device, even if Windows gave it a new path after re-plugging."""
        want = self.config.get("device", "")
        devices = [d for d in _enumerate() if _is_game_controller(d)]
        for d in devices:
            if _path_str(d["path"]) == want:
                return d["path"]
        vid, pid = self.config.get("vendor_id"), self.config.get("product_id")
        for d in devices:
            if d.get("vendor_id") == vid and d.get("product_id") == pid:
                self.config["device"] = _path_str(d["path"])
                return d["path"]
        raise RuntimeError("The trigger device is not connected. Check its USB cable.")

    def select_device(self, device_id: str) -> None:
        self.config["device"] = device_id
        for d in _enumerate():
            if _path_str(d["path"]) == device_id:
                self.config["vendor_id"] = d.get("vendor_id")
                self.config["product_id"] = d.get("product_id")
                self.config["device_name"] = (d.get("product_string") or "").strip()

    def is_configured(self) -> bool:
        return bool(self.config.get("device")) and "byte" in self.config and "mask" in self.config

    def describe(self) -> str:
        if not self.is_configured():
            return "Gamepad button: not set up"
        bit = (self.config["mask"]).bit_length() - 1
        name = self.config.get("device_name") or "game controller"
        return f"{name}, report byte {self.config['byte']} bit {bit}"

    def learn(self, timeout: float = 15.0) -> Dict:
        if _hid is None:
            raise RuntimeError(self.unavailable_reason())
        dev = _Dev(self._resolve_path())
        try:
            byte, mask, active_high = learn_button(dev.read, timeout)
        finally:
            dev.close()
        self.config["byte"], self.config["mask"] = byte, mask
        self.config["active_high"] = active_high
        return dict(self.config)

    def _run(self) -> None:
        if _hid is None:
            raise RuntimeError(self.unavailable_reason())
        byte, mask = int(self.config["byte"]), int(self.config["mask"])
        active_high = bool(self.config.get("active_high", True))
        while not self._stop.is_set():
            try:
                dev = _Dev(self._resolve_path())
            except Exception as exc:  # noqa: BLE001 - keep retrying while unplugged
                if self.on_error:
                    self.on_error(str(exc))
                self._stop.wait(1.0)
                continue
            pressed = False
            try:
                while not self._stop.is_set():
                    report = dev.read(64, 50)
                    t = now()
                    if not report or len(report) <= byte:
                        continue
                    state = bool(report[byte] & mask) == active_high
                    if state and not pressed:
                        self._fire(t)
                    pressed = state
            except OSError as exc:
                if self.on_error:
                    self.on_error(f"Trigger device disconnected ({exc}); reconnecting...")
            finally:
                dev.close()


def learn_button(read, timeout: float) -> Tuple[int, int, bool]:
    """Watch HID reports until one bit toggles; return (byte index, bit mask, active_high).

    ``read(size, timeout_ms)`` returns a list of ints (empty when nothing arrived).
    Bits that change in more than one report before the press (noisy axes) are ignored.
    """
    deadline = time.monotonic() + timeout
    prev: Optional[List[int]] = None
    reports = 0
    noisy: set = set()
    settle_until = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        r = read(64, 50)
        if not r:
            continue
        reports += 1
        if prev is None or len(r) != len(prev):
            prev = r
            continue
        changes = [(i, (a ^ b) & (1 << bit))
                   for i, (a, b) in enumerate(zip(prev, r))
                   for bit in range(8) if (a ^ b) & (1 << bit)]
        if time.monotonic() < settle_until:
            noisy.update(changes)
            prev = r
            continue
        for i, m in changes:
            if (i, m) not in noisy:
                # A bit that clears after a steady baseline means an active-low button.
                # If the very first report was already the press, we cannot tell, so
                # assume the usual active-high button.
                active_high = bool(r[i] & m) or reports <= 2
                return i, m, active_high
        prev = r
    raise TimeoutError("No button press was seen. Check the trigger is in gamepad mode "
                       "and press it (or turn the projector by hand) once.")
