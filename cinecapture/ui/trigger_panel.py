"""Trigger tab: choose the input, learn the button, set and calibrate the delay."""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QRadioButton, QVBoxLayout,
                               QWidget)

from ..core.calibration import CalibrationResult
from ..trigger import create_trigger, source_kinds
from ..trigger.base import TriggerSource
from ..trigger.keyboard import KeyboardTrigger, key_is_risky, key_name
from .widgets import Led, TestStrip

KIND_HELP = {
    "hid": "Recommended. The u-HID Nano set to gamepad mode. Works whatever window is in "
           "front and never sends key presses to other programs.",
    "keyboard": "A u-HID (or similar) left in keyboard mode. In background mode only the "
                "chosen device is listened to. Use a key no program needs, such as F13-F24.",
    "serial": "An Arduino-type board that sends one character per frame over USB.",
    "simulated": "A pretend projector and camera for trying the software without hardware.",
}


class TriggerPanel(QWidget):
    trigger_changed = Signal(object)      # TriggerSource or None
    delay_changed = Signal(float)         # seconds
    test_toggled = Signal(bool)
    suggest_requested = Signal()
    status = Signal(str)
    _learned = Signal(object, str)        # config or None, message

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.trigger: Optional[TriggerSource] = None
        self.waiting_for_ui_key = False
        root = QVBoxLayout(self)

        box = QGroupBox("Trigger input")
        b = QVBoxLayout(box)
        self.kind = QComboBox()
        for key, cls in source_kinds().items():
            label = cls.label + ("" if cls.available() else " (not available)")
            self.kind.addItem(label, key)
        b.addWidget(self.kind)
        self.help = QLabel("")
        self.help.setObjectName("muted")
        self.help.setWordWrap(True)
        b.addWidget(self.help)

        self.mode_row = QWidget()
        mr = QHBoxLayout(self.mode_row)
        mr.setContentsMargins(0, 0, 0, 0)
        self.mode_bg = QRadioButton("Any window (background)")
        self.mode_fg = QRadioButton("Only while this app is active")
        mr.addWidget(self.mode_bg)
        mr.addWidget(self.mode_fg)
        b.addWidget(self.mode_row)

        row = QHBoxLayout()
        self.device = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        row.addWidget(self.device, 1)
        row.addWidget(self.refresh_btn)
        self.device_label = QLabel("Device")
        b.addWidget(self.device_label)
        b.addLayout(row)

        row = QHBoxLayout()
        self.learn_btn = QPushButton("Learn trigger...")
        self.learn_btn.setToolTip("Press the trigger (or turn the projector by hand) once "
                                  "so the app knows which signal to listen for.")
        self.learn_btn.clicked.connect(self.learn)
        self.led = Led()
        self.count = QLabel("0 pulses")
        self.count.setObjectName("value")
        row.addWidget(self.learn_btn)
        row.addStretch()
        row.addWidget(self.led)
        row.addWidget(self.count)
        b.addLayout(row)
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        b.addWidget(self.desc)
        root.addWidget(box)

        tbox = QGroupBox("Timing")
        form = QFormLayout(tbox)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 500)
        self.delay.setDecimals(0)
        self.delay.setSuffix(" ms")
        self.delay.setValue(float(settings["delay_ms"]))
        self.delay.setToolTip("Time to wait after each trigger before taking the next camera "
                              "frame, so the film has settled in the gate.")
        form.addRow("Delay after trigger", self.delay)
        self.debounce = QDoubleSpinBox()
        self.debounce.setRange(0, 200)
        self.debounce.setDecimals(0)
        self.debounce.setSuffix(" ms")
        self.debounce.setValue(float(settings["debounce_ms"]))
        self.debounce.setToolTip("Pulses closer together than this are treated as one "
                                 "(sensor bounce).")
        form.addRow("Ignore repeats within", self.debounce)
        self.rate = QLabel("-")
        self.rate.setObjectName("value")
        form.addRow("Projector speed", self.rate)
        root.addWidget(tbox)

        cbox = QGroupBox("Delay test (blank leader or a test strip, nothing is saved)")
        c = QVBoxLayout(cbox)
        row = QHBoxLayout()
        self.test_btn = QPushButton("Start test")
        self.test_btn.setCheckable(True)
        self.test_btn.toggled.connect(self._on_test)
        self.suggest_btn = QPushButton("Suggest delay")
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.clicked.connect(self.suggest_requested.emit)
        row.addWidget(self.test_btn)
        row.addWidget(self.suggest_btn)
        c.addLayout(row)
        self.strip = TestStrip(8)
        c.addWidget(self.strip)
        self.calib_msg = QLabel("Run the projector at your capture speed, start the test and "
                                "adjust the delay until every frame is sharp (green). "
                                "Or let 'Suggest delay' find it after ~10 frames.")
        self.calib_msg.setWordWrap(True)
        self.calib_msg.setObjectName("muted")
        c.addWidget(self.calib_msg)
        root.addWidget(cbox)
        root.addStretch()

        self._pulses = 0
        self.kind.currentIndexChanged.connect(lambda _: self._kind_changed())
        self.mode_bg.toggled.connect(lambda on: self._mode_changed())
        self.device.currentIndexChanged.connect(lambda _: self._device_changed())
        self.delay.valueChanged.connect(self._on_delay)
        self.debounce.valueChanged.connect(self._on_debounce)
        self._learned.connect(self._learn_finished)
        self._restore()

    # ---------------------------------------------------------------- config
    def _restore(self) -> None:
        cfg = dict(self.settings["trigger"])
        idx = self.kind.findData(cfg.get("kind", "hid"))
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, idx))
        self.kind.blockSignals(False)
        self._make(cfg)

    def _kind_changed(self) -> None:
        self._make({"kind": self.kind.currentData()})

    def _make(self, cfg: dict) -> None:
        if self.trigger is not None:
            self.trigger.stop()
        try:
            self.trigger = create_trigger(cfg)
        except Exception as exc:  # noqa: BLE001
            self.trigger = None
            self.status.emit(str(exc))
        kind = self.kind.currentData()
        self.help.setText(KIND_HELP.get(kind, ""))
        is_kb = kind == "keyboard"
        self.mode_row.setVisible(is_kb)
        if is_kb and isinstance(self.trigger, KeyboardTrigger):
            bg = self.trigger.config.get("mode") == "background"
            self.mode_bg.setEnabled(KeyboardTrigger.background_supported())
            self.mode_bg.blockSignals(True)
            self.mode_bg.setChecked(bg)
            self.mode_fg.setChecked(not bg)
            self.mode_bg.blockSignals(False)
        has_devices = kind in ("hid", "serial", "keyboard")
        self.device.setVisible(has_devices and not (is_kb and not self._kb_background()))
        self.device_label.setVisible(self.device.isVisible())
        self.refresh_btn.setVisible(self.device.isVisible())
        self.learn_btn.setVisible(kind != "simulated")
        if self.trigger is not None and not self.trigger.available():
            self.desc.setText(self.trigger.unavailable_reason())
        self.refresh_devices()
        self._update_desc()
        self._save()
        self.trigger_changed.emit(self.trigger)

    def _kb_background(self) -> bool:
        return isinstance(self.trigger, KeyboardTrigger) and \
            self.trigger.config.get("mode") == "background"

    def _mode_changed(self) -> None:
        if not isinstance(self.trigger, KeyboardTrigger):
            return
        cfg = dict(self.trigger.config)
        cfg["mode"] = "background" if self.mode_bg.isChecked() else "focused"
        self._make(cfg)

    def refresh_devices(self) -> None:
        self.device.blockSignals(True)
        self.device.clear()
        if self.trigger is not None:
            try:
                for d in self.trigger.list_devices():
                    self.device.addItem(d.name + (f"  - {d.hint}" if d.hint else ""), d.id)
            except Exception as exc:  # noqa: BLE001
                self.status.emit(f"Could not list trigger devices: {exc}")
            want = self.trigger.config.get("device")
            idx = self.device.findData(want) if want else -1
            if idx >= 0:
                self.device.setCurrentIndex(idx)
        self.device.blockSignals(False)
        if self.device.count() and self.trigger is not None and \
                not self.trigger.config.get("device") and self.kind.currentData() != "keyboard":
            self._device_changed()

    def _device_changed(self) -> None:
        if self.trigger is None or self.device.currentData() is None:
            return
        if hasattr(self.trigger, "select_device"):
            self.trigger.select_device(self.device.currentData())   # type: ignore[attr-defined]
            self._save()
            self._update_desc()
            self.trigger_changed.emit(self.trigger)

    def _save(self) -> None:
        if self.trigger is not None:
            self.settings["trigger"] = dict(self.trigger.config)
            self.settings.save()

    def _update_desc(self) -> None:
        if self.trigger is None:
            self.desc.setText("No trigger")
            return
        if not self.trigger.available():
            return
        ok = self.trigger.is_configured()
        self.desc.setText(("Ready: " if ok else "Not set up yet: ") + self.trigger.describe() +
                          ("" if ok else ". Press 'Learn trigger'."))

    # ---------------------------------------------------------------- learn
    def learn(self) -> None:
        if self.trigger is None:
            return
        if isinstance(self.trigger, KeyboardTrigger) and not self._kb_background():
            self.waiting_for_ui_key = True
            self.learn_btn.setText("Press the trigger key now...")
            self.learn_btn.setEnabled(False)
            return
        if self.device.isVisible() and self.device.count() == 0 and \
                self.kind.currentData() != "keyboard":
            QMessageBox.information(self, "Trigger", "No device found. Connect it and press "
                                    "Refresh.")
            return
        self.trigger.stop()
        self.learn_btn.setText("Press the trigger now...")
        self.learn_btn.setEnabled(False)
        trig = self.trigger

        def work() -> None:
            try:
                cfg = trig.learn(15.0)
                self._learned.emit(cfg, "")
            except Exception as exc:  # noqa: BLE001
                self._learned.emit(None, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def key_learned_from_ui(self, vk: int) -> None:
        self.waiting_for_ui_key = False
        assert isinstance(self.trigger, KeyboardTrigger)
        self.trigger.learn_from_ui(vk)
        self._learn_finished(dict(self.trigger.config), "")

    def _learn_finished(self, cfg: Optional[dict], err: str) -> None:
        self.learn_btn.setText("Learn trigger...")
        self.learn_btn.setEnabled(True)
        if cfg is None:
            QMessageBox.warning(self, "Trigger", err)
        else:
            self.trigger.config.update(cfg)     # type: ignore[union-attr]
            if isinstance(self.trigger, KeyboardTrigger) and key_is_risky(int(cfg["vk"])):
                QMessageBox.warning(
                    self, "Trigger key",
                    f"The trigger sends '{key_name(int(cfg['vk']))}'. Windows also passes this "
                    "key to whatever program is in front, where it may do something "
                    "unexpected. If your board allows it, choose F13-F24 instead.")
            self.refresh_devices()
            self.status.emit("Trigger learned: " + self.trigger.describe())  # type: ignore[union-attr]
        self._save()
        self._update_desc()
        self.trigger_changed.emit(self.trigger)

    # ---------------------------------------------------------------- timing
    def _on_delay(self, ms: float) -> None:
        self.settings["delay_ms"] = ms
        self.settings.save()
        self.delay_changed.emit(ms / 1000.0)

    def _on_debounce(self, ms: float) -> None:
        self.settings["debounce_ms"] = ms
        self.settings.save()

    def pulse(self) -> None:
        self._pulses += 1
        self.led.flash()
        self.count.setText(f"{self._pulses} pulses")

    def set_rate(self, trigger_fps: float, camera_fps: float, warning: str) -> None:
        if trigger_fps <= 0:
            self.rate.setText(f"- (camera {camera_fps:.0f} fps)")
        else:
            self.rate.setText(f"{trigger_fps:.2f} fps  (camera {camera_fps:.0f} fps)")
        self.rate.setStyleSheet("color: #e5484d;" if warning else "")
        self.rate.setToolTip(warning)

    def _on_test(self, on: bool) -> None:
        self.test_btn.setText("Stop test" if on else "Start test")
        self.suggest_btn.setEnabled(on)
        self.test_toggled.emit(on)

    def set_test_running(self, on: bool) -> None:
        self.test_btn.blockSignals(True)
        self.test_btn.setChecked(on)
        self.test_btn.blockSignals(False)
        self.test_btn.setText("Stop test" if on else "Start test")
        self.suggest_btn.setEnabled(on)

    def show_suggestion(self, result: CalibrationResult) -> None:
        self.calib_msg.setText(result.message)
        if result.delay is not None:
            r = QMessageBox.question(self, "Suggested delay",
                                     result.message + "\n\nUse this delay?")
            if r == QMessageBox.StandardButton.Yes:
                self.delay.setValue(round(result.delay * 1000))

    def set_capturing(self, on: bool) -> None:
        for w in (self.kind, self.device, self.refresh_btn, self.learn_btn, self.mode_bg,
                  self.mode_fg, self.test_btn, self.debounce):
            w.setEnabled(not on)
        if not on:
            self.mode_bg.setEnabled(KeyboardTrigger.background_supported())


