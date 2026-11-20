"""Trigger inputs: the projector's frame sensor reaches the app through one of these.

Every source has the same shape (see :class:`~.base.TriggerSource`), so new
kinds of trigger hardware can be added as a new module without changing the
rest of the app. Register them in :data:`SOURCES`.
"""

from __future__ import annotations

from typing import Dict, Type

from .base import TriggerSource  # noqa: F401


def _sources() -> Dict[str, Type[TriggerSource]]:
    from .hid_gamepad import HidGamepadTrigger
    from .keyboard import KeyboardTrigger
    from .serial_port import SerialTrigger
    from .simulated import SimulatedTriggerSource
    return {
        HidGamepadTrigger.kind: HidGamepadTrigger,
        KeyboardTrigger.kind: KeyboardTrigger,
        SerialTrigger.kind: SerialTrigger,
        SimulatedTriggerSource.kind: SimulatedTriggerSource,
    }


def source_kinds() -> Dict[str, Type[TriggerSource]]:
    return _sources()


def create_trigger(config: dict) -> TriggerSource:
    kind = config.get("kind", "hid")
    cls = _sources().get(kind)
    if cls is None:
        raise ValueError(f"unknown trigger type {kind!r}")
    return cls(config)
