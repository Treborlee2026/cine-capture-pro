"""Camera tab: choose the camera and mode, set and lock exposure, gain, white balance."""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..camera import available_backends, create_camera
from ..camera.base import Camera, CameraSettingsLock, DeviceInfo, VideoMode
from ..core import analysis
from .widgets import ControlRow, FocusLoupe

BACKEND_NAMES = {"dshow": "DirectShow (recommended)", "msmf": "Media Foundation",
                 "avfoundation": "AVFoundation", "v4l2": "Video4Linux",
                 "simulated": "Simulated camera (demo)"}


class CameraPanel(QWidget):
    camera_changed = Signal(object)     # Camera or None
    status = Signal(str)
    lock_changed = Signal(bool)
    _leader_done = Signal(str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.camera: Optional[Camera] = None
        self.lock = CameraSettingsLock()
        self._devices: List[DeviceInfo] = []
        self._rows: Dict[str, ControlRow] = {}
        self.latest_preview = None          # set by the main window (BGR image)

        root = QVBoxLayout(self)

        box = QGroupBox("Camera")
        b = QVBoxLayout(box)
        self.backend = QComboBox()
        for key in available_backends():
            self.backend.addItem(BACKEND_NAMES.get(key, key), key)
        b.addWidget(QLabel("Connection type"))
        b.addWidget(self.backend)
        row = QHBoxLayout()
        self.device = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_devices)
        row.addWidget(self.device, 1)
        row.addWidget(refresh)
        b.addWidget(QLabel("Camera"))
        b.addLayout(row)
        b.addWidget(QLabel("Resolution and format"))
        self.mode = QComboBox()
        b.addWidget(self.mode)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Connect")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self.toggle_open)
        self.driver_btn = QPushButton("Driver settings...")
        self.driver_btn.setToolTip("Open the camera driver's own settings window")
        self.driver_btn.clicked.connect(self._driver_dialog)
        row.addWidget(self.open_btn, 1)
        row.addWidget(self.driver_btn)
        b.addLayout(row)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        b.addWidget(self.info)
        root.addWidget(box)

        self.ctl_box = QGroupBox("Picture settings (recorded by the camera)")
        self.ctl_layout = QVBoxLayout(self.ctl_box)
        row = QHBoxLayout()
        self.leader_btn = QPushButton("Set from leader")
        self.leader_btn.setToolTip("With clear leader in the gate: sets exposure just below "
                                   "clipping and neutral white balance, then you fine-tune.")
        self.leader_btn.clicked.connect(self.set_from_leader)
        self.lock_btn = QPushButton("Lock settings")
        self.lock_btn.setCheckable(True)
        self.lock_btn.toggled.connect(self._on_lock)
        row.addWidget(self.leader_btn)
        row.addWidget(self.lock_btn)
        self.ctl_layout.addLayout(row)
        row = QHBoxLayout()
        self.preset = QComboBox()
        save_p = QPushButton("Save preset")
        save_p.clicked.connect(self._save_preset)
        load_p = QPushButton("Load")
        load_p.clicked.connect(self._load_preset)
        row.addWidget(self.preset, 1)
        row.addWidget(load_p)
        row.addWidget(save_p)
        self.ctl_layout.addLayout(row)
        self.controls_host = QVBoxLayout()
        self.ctl_layout.addLayout(self.controls_host)
        root.addWidget(self.ctl_box)

        focus = QGroupBox("Focus (turn the lens until the bar is full and green)")
        f = QVBoxLayout(focus)
        self.loupe = FocusLoupe()
        f.addWidget(self.loupe)
        reset = QPushButton("Reset peak")
        reset.clicked.connect(self.loupe.reset_peak)
        f.addWidget(reset, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(focus, 1)

        self._leader_done.connect(self._leader_finished)
        self.backend.currentIndexChanged.connect(lambda _: self.refresh_devices())
        self.device.currentIndexChanged.connect(lambda _: self._on_device_selected())
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._reload_presets()
        self._restore_backend()
        self._set_connected(False)

    # ---------------------------------------------------------------- devices
    def _restore_backend(self) -> None:
        want = self.settings["camera"].get("backend")
        idx = self.backend.findData(want) if want else 0
        self.backend.setCurrentIndex(max(0, idx))
        self.refresh_devices()

    def _make_camera(self) -> Camera:
        return create_camera(self.backend.currentData())

    def refresh_devices(self) -> None:
        if self.camera is not None and self.camera.is_open:
            return
        try:
            self._devices = self._make_camera().list_devices()
        except Exception as exc:  # noqa: BLE001
            self._devices = []
            self.info.setText(f"Could not list cameras: {exc}")
        self.device.blockSignals(True)
        self.device.clear()
        for d in self._devices:
            self.device.addItem(d.name, d.index)
        want = self.settings["camera"].get("device_name")
        idx = self.device.findText(want) if want else -1
        self.device.setCurrentIndex(idx if idx >= 0 else 0)
        self.device.blockSignals(False)
        if not self._devices:
            self.info.setText("No camera found. Check the USB cable, then press Refresh.")
        self._on_device_selected()

    def _on_device_selected(self) -> None:
        self.mode.clear()
        self.mode.addItem("Best available (largest MJPEG)", None)

    def _fill_modes(self, modes: List[VideoMode], current: Optional[VideoMode]) -> None:
        self.mode.blockSignals(True)
        self.mode.clear()
        for m in modes:
            self.mode.addItem(str(m), m)
        if current is not None:
            for i in range(self.mode.count()):
                m = self.mode.itemData(i)
                if m and (m.width, m.height, m.fourcc) == (current.width, current.height,
                                                            current.fourcc):
                    self.mode.setCurrentIndex(i)
        self.mode.blockSignals(False)

    def _mode_changed(self, _i: int) -> None:
        if self.camera is None or not self.camera.is_open or self.lock.locked:
            return
        mode = self.mode.currentData()
        if mode is None:
            return
        self._reopen(mode)

    # ------------------------------------------------------------ open/close
    def toggle_open(self) -> None:
        if self.camera is not None and self.camera.is_open:
            self.close_camera()
        else:
            self.open_camera()

    def open_camera(self, mode: Optional[VideoMode] = None) -> None:
        if not self._devices:
            self.refresh_devices()
            if not self._devices:
                return
        dev = self._devices[max(0, self.device.currentIndex())]
        if mode is None:
            saved = self.settings["camera"].get("mode")
            if saved and self.settings["camera"].get("device_name") == dev.name:
                mode = VideoMode(**saved)
        cam = self._make_camera()
        try:
            actual = cam.open(dev, mode)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Camera", str(exc))
            return
        self.camera = cam
        cam.on_reconnect = self._on_reconnect
        self.settings["camera"].update(backend=self.backend.currentData(),
                                       device_name=dev.name,
                                       mode={"width": actual.width, "height": actual.height,
                                             "fps": actual.fps, "fourcc": actual.fourcc})
        self.settings.save()
        try:
            modes = cam.list_modes()
        except Exception:  # noqa: BLE001
            modes = []
        self._fill_modes(modes or [actual], actual)
        jpeg = ("Saving the camera's own JPEG data (no re-encoding)." if cam.delivers_jpeg
                else "This mode delivers uncompressed frames.")
        self.info.setText(f"{actual}. {jpeg}")
        self._build_controls()
        if self.lock.locked:
            self.lock.apply(cam)
        self._set_connected(True)
        self.camera_changed.emit(cam)

    def _reopen(self, mode: VideoMode) -> None:
        self.close_camera(emit=False)
        self.open_camera(mode)

    def close_camera(self, emit: bool = True) -> None:
        if self.camera is not None:
            self.camera.close()
        self.camera = None
        self._set_connected(False)
        if emit:
            self.camera_changed.emit(None)

    def _set_connected(self, on: bool) -> None:
        self.open_btn.setText("Disconnect" if on else "Connect")
        self.backend.setEnabled(not on)
        self.device.setEnabled(not on)
        self.driver_btn.setEnabled(on)
        self.ctl_box.setEnabled(on)
        if not on:
            self._clear_controls()

    def _on_reconnect(self) -> None:
        # Called from the camera thread: put the locked values back straight away.
        if self.lock.locked and self.camera is not None:
            self.lock.apply(self.camera)
        self.status.emit("Camera reconnected; locked settings re-applied.")

    # --------------------------------------------------------------- controls
    def _clear_controls(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows = {}

    def _build_controls(self) -> None:
        self._clear_controls()
        if self.camera is None:
            return
        for c in self.camera.controls():
            row = ControlRow(c.key, c.name, c.minimum, c.maximum, c.step, c.value, c.has_auto,
                             c.auto, c.unit, c.note)
            row.value_changed.connect(self._set_value)
            row.auto_changed.connect(self._set_auto)
            self.controls_host.addWidget(row)
            self._rows[c.key] = row
        # Recommended starting point: automatic modes off.
        for key in ("exposure", "white_balance"):
            if key in self._rows and self._rows[key].auto.isVisible():
                self._rows[key].auto.setChecked(False)
                self._set_auto(key, False)
        self._apply_lock_state()

    def _set_value(self, key: str, value: float) -> None:
        if self.camera is None:
            return
        try:
            self.camera.set_control(key, value)
        except Exception as exc:  # noqa: BLE001
            self.status.emit(str(exc))
        actual = self.camera.get_control(key)
        if actual is not None and key in self._rows:
            self._rows[key].set_value(actual)

    def _set_auto(self, key: str, auto: bool) -> None:
        if self.camera is not None:
            try:
                self.camera.set_auto(key, auto)
            except Exception as exc:  # noqa: BLE001
                self.status.emit(str(exc))

    def refresh_values(self) -> None:
        if self.camera is None:
            return
        for key, row in self._rows.items():
            v = self.camera.get_control(key)
            if v is not None:
                row.set_value(v)

    # ------------------------------------------------------------------ lock
    def _on_lock(self, on: bool) -> None:
        if self.camera is None:
            self.lock_btn.setChecked(False)
            return
        if on:
            for key, row in self._rows.items():
                if row.auto.isVisible() and row.auto.isChecked():
                    row.auto.setChecked(False)
            self.lock.lock(self.camera)
            self.lock.apply(self.camera)
            self.status.emit("Camera settings locked for this reel.")
        else:
            self.lock.unlock()
            self.status.emit("Camera settings unlocked.")
        self._apply_lock_state()
        self.lock_changed.emit(on)

    def _apply_lock_state(self) -> None:
        locked = self.lock.locked
        self.lock_btn.blockSignals(True)
        self.lock_btn.setChecked(locked)
        self.lock_btn.blockSignals(False)
        self.lock_btn.setText("Locked (click to unlock)" if locked else "Lock settings")
        for row in self._rows.values():
            row.set_locked(locked)
        self.leader_btn.setEnabled(not locked)
        self.mode.setEnabled(not locked)

    def set_lock_from_reel(self, data: dict) -> None:
        if not data:
            return
        self.lock = CameraSettingsLock.from_dict(data)
        if self.camera is not None and self.lock.locked:
            self.lock.apply(self.camera)
            self.refresh_values()
        self._apply_lock_state()

    def check_drift(self) -> List[str]:
        """Re-apply locked settings if the camera has changed them. Returns messages."""
        if self.camera is None or not self.lock.locked:
            return []
        drifts = self.lock.check(self.camera)
        if not drifts:
            return []
        self.lock.apply(self.camera)
        return [f"{d.key} had changed ({d.actual:g} instead of {d.locked:g}); restored."
                for d in drifts]

    # ---------------------------------------------------------------- presets
    def _reload_presets(self) -> None:
        self.preset.clear()
        for name in sorted(self.settings["presets"]):
            self.preset.addItem(name)

    def _save_preset(self) -> None:
        if self.camera is None:
            return
        name, ok = QInputDialog.getText(self, "Save preset",
                                        "Preset name (e.g. Super 8 Kodachrome):")
        if not ok or not name.strip():
            return
        tmp = CameraSettingsLock()
        tmp.lock(self.camera)
        self.settings["presets"][name.strip()] = {"values": tmp.values}
        self.settings.save()
        self._reload_presets()
        self.preset.setCurrentText(name.strip())

    def _load_preset(self) -> None:
        name = self.preset.currentText()
        data = self.settings["presets"].get(name)
        if not data or self.camera is None:
            return
        tmp = CameraSettingsLock(values=dict(data["values"]))
        failed = tmp.apply(self.camera)
        self.refresh_values()
        for key, row in self._rows.items():
            if key in tmp.values and row.auto.isVisible():
                row.auto.setChecked(False)
        self.status.emit(f"Preset '{name}' loaded." + (f" Not applied: {', '.join(failed)}"
                                                       if failed else ""))

    # ---------------------------------------------------------- leader setup
    def set_from_leader(self) -> None:
        if self.camera is None:
            return
        r = QMessageBox.question(self, "Set from leader",
                                 "Put clear (transparent) leader in the gate with the lamp on, "
                                 "then press Yes. This takes about 5 seconds.")
        if r != QMessageBox.StandardButton.Yes:
            return
        self.leader_btn.setEnabled(False)
        self.status.emit("Measuring the leader...")
        threading.Thread(target=self._leader_worker, daemon=True).start()

    def _leader_worker(self) -> None:
        cam = self.camera
        if cam is None:
            return
        msgs = []
        try:
            keys = {c.key: c for c in cam.controls()}
            # 1. Let the camera find a starting point on its own, then freeze it.
            for key in ("exposure", "white_balance"):
                if key in keys and keys[key].has_auto:
                    cam.set_auto(key, True)
            time.sleep(2.5)
            for key in ("exposure", "white_balance"):
                if key in keys and keys[key].has_auto:
                    v = cam.get_control(key)
                    cam.set_auto(key, False)
                    if v is not None:
                        cam.set_control(key, v)
            # 2. Step exposure down until clear leader no longer clips.
            if "exposure" in keys:
                for _ in range(8):
                    time.sleep(0.6)
                    img = self.latest_preview
                    if img is None:
                        break
                    clip = analysis.clipped_fraction(img, 250)
                    if clip < 0.001:
                        break
                    v = cam.get_control("exposure")
                    if v is None or v - keys["exposure"].step < keys["exposure"].minimum:
                        break
                    cam.set_control("exposure", v - keys["exposure"].step)
                msgs.append(f"exposure {cam.get_control('exposure'):g}")
            if "white_balance" in keys:
                msgs.append(f"white balance {cam.get_control('white_balance'):g} K")
            self._leader_done.emit("Set from leader: " + ", ".join(msgs) +
                                   ". Fine-tune if needed, then Lock.")
        except Exception as exc:  # noqa: BLE001
            self._leader_done.emit(f"Set from leader failed: {exc}")

    def _leader_finished(self, msg: str) -> None:
        self.refresh_values()
        for key in ("exposure", "white_balance"):
            if key in self._rows:
                self._rows[key].auto.blockSignals(True)
                self._rows[key].auto.setChecked(False)
                self._rows[key].auto.blockSignals(False)
                self._rows[key].set_locked(False)
        self.leader_btn.setEnabled(True)
        self.status.emit(msg)

    def _driver_dialog(self) -> None:
        if self.camera is None or not self.camera.show_driver_dialog():
            QMessageBox.information(self, "Driver settings",
                                    "This connection type has no driver settings window. "
                                    "Try 'DirectShow' as the connection type.")
        else:
            self.refresh_values()
