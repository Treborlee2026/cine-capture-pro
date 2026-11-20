"""Common interface for trigger inputs."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

TriggerCallback = Callable[[float, str], None]     # (timestamp from core.frames.now(), source)


@dataclass
class TriggerDevice:
    """A device the user can pick in Settings."""

    id: str                 # stable identifier stored in the config
    name: str               # shown to the user
    hint: str = ""          # e.g. "Ultimarc device"
    recommended: bool = False


class TriggerSource:
    """Base class for trigger inputs.

    Subclasses read their hardware on a background thread and call the
    callback with a timestamp taken the moment the pulse arrives.
    ``config`` is a plain dict saved in the app settings.
    """

    kind = "base"
    label = "Trigger"
    works_in_background = True

    def __init__(self, config: Optional[Dict] = None) -> None:
        self.config: Dict = dict(config or {})
        self.config["kind"] = self.kind
        self._callback: Optional[TriggerCallback] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- discovery
    @classmethod
    def available(cls) -> bool:
        """False when the library or platform support for this input is missing."""
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        return ""

    def list_devices(self) -> List[TriggerDevice]:
        return []

    # --------------------------------------------------------------- learning
    def learn(self, timeout: float = 15.0) -> Dict:
        """Wait for the user to fire the trigger once; return config describing it.

        Blocks; call from a worker thread. Raises TimeoutError if nothing arrives.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """Human-readable summary of the configured input."""
        return self.label

    def is_configured(self) -> bool:
        return True

    # --------------------------------------------------------------- running
    def start(self, callback: TriggerCallback) -> None:
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_safe, name=f"Trigger-{self.kind}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake()
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _fire(self, timestamp: float) -> None:
        if self._callback is not None:
            self._callback(timestamp, self.kind)

    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            if self.on_error:
                self.on_error(f"{self.label}: {exc}")

    def _run(self) -> None:
        raise NotImplementedError

    def _wake(self) -> None:
        """Interrupt a blocking read so the thread can exit (optional)."""
