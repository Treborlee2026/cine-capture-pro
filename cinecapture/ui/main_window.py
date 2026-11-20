"""Main window: the Capture screen."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QScrollArea, QTabWidget, QToolBar, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__
from ..camera.base import Camera
from ..core.calibration import check_rate, suggest_delay
from ..core.frames import Frame, now
from ..core.report import write_report
from ..core.session import CaptureSession, RateMeter, SessionSettings
from ..trigger.base import TriggerSource
from ..trigger.keyboard import KeyboardTrigger
from .camera_panel import CameraPanel
from .capture_panel import CapturePanel
from .preview import PreviewWidget
from .preview_worker import LatestFrame, PreviewWorker
from .settings_store import Settings
from .trigger_panel import TriggerPanel
from .widgets import HistogramWidget


class Bridge(QObject):
    """Carries events from background threads onto the interface thread."""

    session_event = Signal(str, object)
    pulse = Signal()
    message = Signal(str)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None, demo: bool = False) -> None:
        super().__init__()
        self.settings = settings or Settings()
        if demo:
            self.settings["camera"]["backend"] = "simulated"
            self.settings["trigger"] = {"kind": "simulated"}
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 950)

        self.bridge = Bridge()
        self.latest = LatestFrame()
        self.camera: Optional[Camera] = None
        self.trigger: Optional[TriggerSource] = None
        self.session: Optional[CaptureSession] = None
        self.trigger_rate = RateMeter(10)
        self.camera_rate = RateMeter(30)
        self._alarm_text = ""
        self.non_interactive = False      # set by the automated smoke test: no pop-ups

        # ------------------------------------------------------- central area
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        self.banner.mousePressEvent = lambda e: self.clear_alarm()   # type: ignore[assignment]
        lay.addWidget(self.banner)
        self.preview = PreviewWidget()
        lay.addWidget(self.preview, 1)
        self.setCentralWidget(central)

        # ------------------------------------------------------------- panels
        self.camera_panel = CameraPanel(self.settings)
        self.trigger_panel = TriggerPanel(self.settings)
        self.capture_panel = CapturePanel(self.settings)
        tabs = QTabWidget()
        tabs.addTab(_scroll(self.camera_panel), "1  Camera")
        tabs.addTab(_scroll(self.trigger_panel), "2  Trigger")
        tabs.addTab(_scroll(self.capture_panel), "3  Capture")
        self.tabs = tabs
        dock = QDockWidget("Setup and capture", self)
        dock.setObjectName("controls")
        dock.setWidget(tabs)
        dock.setMinimumWidth(430)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self.histogram = HistogramWidget()
        hdock = QDockWidget("Histogram", self)
        hdock.setObjectName("histogram")
        hdock.setWidget(self.histogram)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, hdock)
        self.hist_dock = hdock

        self._build_toolbar()
        self._build_menu()
        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)

        # --------------------------------------------------------- the worker
        self.worker = PreviewWorker(self.latest, self)
        self._apply_view_options()
        self.worker.preview_ready.connect(self._on_preview)
        self.worker.loupe_ready.connect(self.camera_panel.loupe.update_crop)
        self.worker.start()

        # ---------------------------------------------------------- wiring
        self.bridge.session_event.connect(self._on_session_event)
        self.bridge.pulse.connect(self.trigger_panel.pulse)
        self.bridge.message.connect(self.show_status)
        self.camera_panel.camera_changed.connect(self._on_camera_changed)
        self.camera_panel.status.connect(self.show_status)
        self.trigger_panel.trigger_changed.connect(self._on_trigger_changed)
        self.trigger_panel.status.connect(self.show_status)
        self.trigger_panel.delay_changed.connect(self._on_delay_changed)
        self.trigger_panel.test_toggled.connect(self._on_test_toggled)
        self.trigger_panel.suggest_requested.connect(self._on_suggest)
        self.capture_panel.start_requested.connect(self.start_capture)
        self.capture_panel.stop_requested.connect(self.stop_capture)
        self.capture_panel.pause_requested.connect(self._on_pause)
        self.capture_panel.reel_changed.connect(self._on_reel_changed)
        self.capture_panel.status.connect(self.show_status)
        self.preview.zoom_changed.connect(lambda z: None)

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._tick)
        self.stats_timer.start(250)
        self.drift_timer = QTimer(self)
        self.drift_timer.timeout.connect(self._check_drift)
        self.drift_timer.start(5000)
        self.alarm_timer = QTimer(self)
        self.alarm_timer.timeout.connect(QApplication.beep)

        self._on_trigger_changed(self.trigger_panel.trigger)
        self.capture_panel.reopen_last()
        if demo:
            QTimer.singleShot(200, self.camera_panel.open_camera)
        self.show_status("Ready. Step 1: connect the camera.")

    # ============================================================== toolbar
    def _build_toolbar(self) -> None:
        tb = QToolBar("View")
        tb.setObjectName("view")
        self.addToolBar(tb)
        v = self.settings["view"]

        def toggle(text: str, key: str, tip: str) -> QAction:
            a = QAction(text, self)
            a.setCheckable(True)
            a.setChecked(bool(v.get(key)))
            a.setToolTip(tip)
            a.toggled.connect(lambda on, k=key: self._set_view(k, on))
            tb.addAction(a)
            return a

        fit = QAction("Fit", self)
        fit.setToolTip("Fit the picture to the window (double-click the picture to zoom in)")
        fit.triggered.connect(self.preview.fit)
        tb.addAction(fit)
        z100 = QAction("100%", self)
        z100.triggered.connect(lambda: self.preview.set_zoom_100())
        tb.addAction(z100)
        tb.addSeparator()
        toggle("Guides", "guides", "Show the frame guide for the reel's gauge")
        toggle("Focus peaking", "peaking", "Highlight sharp edges in red")
        toggle("Zebra", "zebra", "Stripe overexposed (clipped) areas")
        toggle("Histogram", "histogram", "Show the RGB histogram")
        tb.addSeparator()
        toggle("Flip H", "flip_h", "Mirror left to right")
        toggle("Flip V", "flip_v", "Turn upside down")
        rot = QAction("Rotate 90", self)
        rot.triggered.connect(self._rotate)
        tb.addAction(rot)

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        a = m.addAction("New project...")
        a.triggered.connect(self.capture_panel.new_project)
        a = m.addAction("Open project...")
        a.setShortcut(QKeySequence.StandardKey.Open)
        a.triggered.connect(lambda: self.capture_panel.open_project())
        a = m.addAction("Open reel folder")
        a.triggered.connect(self._open_reel_folder)
        m.addSeparator()
        a = m.addAction("Export settings...")
        a.triggered.connect(self._export_settings)
        a = m.addAction("Import settings...")
        a.triggered.connect(self._import_settings)
        a = m.addAction("Reset settings")
        a.triggered.connect(self._reset_settings)
        m.addSeparator()
        a = m.addAction("Quit")
        a.setShortcut(QKeySequence.StandardKey.Quit)
        a.triggered.connect(self.close)
        c = self.menuBar().addMenu("&Capture")
        a = c.addAction("Start capture")
        a.setShortcut("Ctrl+R")
        a.triggered.connect(self.start_capture)
        a = c.addAction("Stop capture")
        a.setShortcut("Ctrl+.")
        a.triggered.connect(self.stop_capture)
        h = self.menuBar().addMenu("&Help")
        a = h.addAction("Setup guide (online)")
        a.triggered.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/Treborlee2026/cine-capture-pro/blob/main/docs/USER_GUIDE.md")))
        a = h.addAction("About")
        a.triggered.connect(self._about)

    def _set_view(self, key: str, on: bool) -> None:
        self.settings["view"][key] = on
        self.settings.save()
        self._apply_view_options()

    def _rotate(self) -> None:
        v = self.settings["view"]
        v["rotate"] = (int(v.get("rotate", 0)) + 90) % 360
        self.settings.save()
        self._apply_view_options()

    def _apply_view_options(self) -> None:
        v = self.settings["view"]
        o = self.worker.options
        o.flip_h, o.flip_v = bool(v["flip_h"]), bool(v["flip_v"])
        o.rotate = int(v["rotate"])
        o.peaking, o.zebra = bool(v["peaking"]), bool(v["zebra"])
        o.histogram = bool(v["histogram"])
        self.worker.fps = float(v.get("preview_fps", 20))
        self.preview.guides = bool(v["guides"])
        self.hist_dock.setVisible(bool(v["histogram"]))
        self.preview.update()

    # =============================================================== camera
    def _on_camera_changed(self, cam: Optional[Camera]) -> None:
        if self.session is not None and cam is None:
            self.stop_capture()
        self.camera = cam
        if cam is None:
            self.preview.clear("No camera. Open the Camera tab and press Connect.")
            return
        cam.on_frame = self._on_camera_frame
        cam.on_disconnect = lambda: self.bridge.message.emit(
            "Camera disconnected. Trying to reconnect...")
        self.show_status("Camera connected. Step 2: set exposure and focus, then Lock.")

    def _on_camera_frame(self, frame: Frame) -> None:
        """Camera thread: keep this fast."""
        self.camera_rate.tick(frame.timestamp)
        session = self.session
        if session is not None:
            session.on_camera_frame(frame)
        if not (self.settings["view"].get("show_captured") and session is not None
                and session.state == "capturing"):
            self.latest.put(frame)

    def _on_preview(self, res) -> None:
        self.preview.set_image(res.image, res.source_width)
        self.camera_panel.latest_preview = res.image
        if res.histogram is not None:
            self.histogram.set_histogram(res.histogram, res.clipped)

    def _check_drift(self) -> None:
        for msg in self.camera_panel.check_drift():
            self.show_status("Camera: " + msg)
            if self.session is not None:
                self.session._log.write("camera_drift", 0.0, detail=msg)

    # ============================================================== trigger
    def _on_trigger_changed(self, trig: Optional[TriggerSource]) -> None:
        if self.trigger is not None and self.trigger is not trig:
            self.trigger.stop()
        self.trigger = trig
        if trig is None or not trig.available() or not trig.is_configured():
            return
        trig.on_error = self.bridge.message.emit
        try:
            trig.stop()
            trig.start(self._on_trigger)
        except Exception as exc:  # noqa: BLE001
            self.show_status(f"Trigger could not start: {exc}")

    def _on_trigger(self, timestamp: float, source: str) -> None:
        """Trigger thread."""
        self.trigger_rate.tick(timestamp)
        session = self.session
        if session is not None:
            session.on_trigger(timestamp, source)
        self.bridge.pulse.emit()

    def keyPressEvent(self, e) -> None:
        if self._keyboard_event(e, True):
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if self._keyboard_event(e, False):
            return
        super().keyReleaseEvent(e)

    def _keyboard_event(self, e, down: bool) -> bool:
        vk = int(e.nativeVirtualKey()) if hasattr(e, "nativeVirtualKey") else 0
        if not vk:
            return False
        if down and self.trigger_panel.waiting_for_ui_key:
            self.trigger_panel.key_learned_from_ui(vk)
            return True
        trig = self.trigger
        if isinstance(trig, KeyboardTrigger):
            if e.isAutoRepeat() and down:
                return trig.key_from_ui(vk, True)
            return trig.key_from_ui(vk, down, now())
        return False

    def _on_delay_changed(self, delay: float) -> None:
        if self.session is not None:
            self.session.set_delay(delay)

    # ============================================================ test mode
    def _session_settings(self) -> SessionSettings:
        s = self.settings
        return SessionSettings(
            delay=float(s["delay_ms"]) / 1000.0, debounce=float(s["debounce_ms"]) / 1000.0,
            file_format=self.capture_panel.fmt.currentData(),
            jpeg_quality=int(s["jpeg_quality"]),
            buffer_bytes=int(float(s["buffer_gb"]) * 1024 ** 3),
            end_of_reel=bool(s["end_of_reel"]),
            end_of_reel_frames=int(s["end_of_reel_frames"]))

    def _ready_to_run(self, what: str) -> bool:
        if self.camera is None:
            QMessageBox.information(self, what, "Connect the camera first (Camera tab).")
            self.tabs.setCurrentIndex(0)
            return False
        if self.trigger is None or not self.trigger.is_configured():
            QMessageBox.information(self, what, "Set up the trigger first (Trigger tab).")
            self.tabs.setCurrentIndex(1)
            return False
        return True

    def _on_test_toggled(self, on: bool) -> None:
        if on:
            if self.session is not None or not self._ready_to_run("Delay test"):
                self.trigger_panel.set_test_running(False)
                return
            self.session = CaptureSession(None, self._session_settings())
            self.session.subscribe(self._session_listener)
            self.session.start_test()
            self.show_status("Delay test running. Start the projector.")
        else:
            if self.session is not None and self.session.mode == "test":
                self.session.stop()
                self.session = None
            self.show_status("Delay test stopped.")

    def _on_suggest(self) -> None:
        if self.session is None or self.session.recording is None:
            return
        self.trigger_panel.show_suggestion(suggest_delay(self.session.recording))

    # ============================================================== capture
    def start_capture(self) -> None:
        reel = self.capture_panel.reel
        if self.session is not None and self.session.mode == "test":
            self.trigger_panel.set_test_running(False)
            self._on_test_toggled(False)
        if self.session is not None:
            return
        if reel is None:
            QMessageBox.information(self, "Capture", "Create or open a project and a reel first.")
            self.tabs.setCurrentIndex(2)
            return
        if not self._ready_to_run("Capture"):
            return
        if not self.camera_panel.lock.locked:
            r = QMessageBox.question(
                self, "Lock camera settings?",
                "The camera settings are not locked, so exposure or colour could change during "
                "the reel. Lock them now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Yes:
                self.camera_panel.lock_btn.setChecked(True)
        first = self.capture_panel.start_frame.value()
        existing = os.path.join(reel.frames_dir, f"{reel.prefix}_{first:06d}")
        if any(os.path.exists(existing + ext) for ext in (".jpg", ".tif", ".png")):
            r = QMessageBox.warning(self, "Capture", f"Frame {first} already exists in this "
                                    "reel and would be overwritten. Continue?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        settings = self._session_settings()
        settings.prefix = reel.prefix
        reel.file_format = settings.file_format
        reel.trigger_delay = settings.delay
        reel.camera = self.camera_panel.lock.to_dict()
        reel.save()
        self.session = CaptureSession(reel.frames_dir, settings, first_number=first,
                                      log_path=reel.log_path)
        self.session.subscribe(self._session_listener)
        self._session_started = datetime.now()
        try:
            self.session.start_capture()
        except Exception as exc:  # noqa: BLE001
            self.session = None
            QMessageBox.critical(self, "Capture", f"Could not start: {exc}")
            return
        self.trigger_panel.set_capturing(True)
        self.camera_panel.ctl_box.setEnabled(False)
        self.show_status("Capturing. Start the projector.")

    def stop_capture(self) -> None:
        session = self.session
        if session is None or session.mode != "capture":
            return
        self.show_status("Finishing writing frames...")
        QApplication.processEvents()
        stats = session.stop()
        self.session = None
        reel = self.capture_panel.reel
        report = ""
        if reel is not None:
            if stats.last_number:
                reel.next_frame = stats.last_number + 1
            reel.frames_captured += stats.saved
            reel.save()
            cam = dict(self.camera_panel.lock.to_dict())
            cam["name"] = self.settings["camera"].get("device_name", "")
            cam["mode"] = str(self.camera.mode) if self.camera and self.camera.mode else ""
            try:
                report, _ = write_report(reel, stats, session.settings, cam,
                                         getattr(self, "_session_started", None))
            except OSError as exc:
                self.show_status(f"Report not written: {exc}")
            self.capture_panel._reel_selected()
        self.trigger_panel.set_capturing(False)
        self.camera_panel.ctl_box.setEnabled(self.camera is not None)
        self.capture_panel.set_state("idle")
        ok = stats.dropped == 0 and stats.duplicates == 0 and stats.write_errors == 0
        text = (f"{stats.saved} frames saved, {stats.dropped} dropped, "
                f"{stats.duplicates} duplicates.")
        self.last_stats = stats
        if self.non_interactive:
            self.show_status("Capture finished. " + text)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Capture finished")
        box.setIcon(QMessageBox.Icon.Information if ok else QMessageBox.Icon.Warning)
        box.setText(text + ("" if ok else "\nSee the session report for the frame numbers."))
        if report:
            box.setInformativeText(f"Report: {report}")
        box.exec()
        self.show_status("Capture finished. " + text)

    def _on_pause(self, paused: bool) -> None:
        if self.session is None:
            return
        if paused:
            self.session.pause("Capture paused by you. Triggers are counted as dropped until "
                               "you resume.")
        else:
            self.session.resume()
            self.clear_alarm()

    def _on_reel_changed(self, reel) -> None:
        if reel is None:
            return
        self.preview.gauge = reel.gauge
        self.preview.update()
        if reel.camera:
            self.camera_panel.set_lock_from_reel(reel.camera)
        if reel.trigger_delay:
            self.trigger_panel.delay.setValue(round(reel.trigger_delay * 1000))

    # ============================================================== events
    def _session_listener(self, event: str, data) -> None:
        """Session threads -> interface thread."""
        self.bridge.session_event.emit(event, data)

    def _on_session_event(self, event: str, data) -> None:
        s = self.session
        if event == "state":
            self.capture_panel.set_state(data)
            if data == "paused":
                self.capture_panel.pause_btn.blockSignals(True)
                self.capture_panel.pause_btn.setChecked(True)
                self.capture_panel.pause_btn.blockSignals(False)
                self.capture_panel.set_state("paused")
        elif event == "match":
            if s is not None and s.mode == "test":
                self.trigger_panel.strip.set_items(list(s.test_strip))
            elif self.settings["view"].get("show_captured") and data.get("preview") is not None:
                self.preview.set_image(data["preview"], self.preview._source_w or 1)
        elif event == "drop":
            self.show_status(f"Frame {data['number']} dropped: {data['reason']}")
        elif event == "duplicate":
            self.show_status(f"Frame {data['number']}: the camera repeated the previous image")
        elif event == "write_error":
            self.raise_alarm(f"Frame {data['number']} could not be saved: {data['message']}")
        elif event == "alarm":
            self.raise_alarm(str(data))
        elif event == "warning":
            self.show_status(str(data))
        elif event == "end_of_reel":
            kind = {"clear": "clear leader", "dark": "black leader"}.get(data["kind"], "no picture")
            if self.capture_panel.eor.isChecked():
                self.raise_alarm(f"End of reel: {kind} detected at frame {data['number']}. "
                                 "Capture stopped. Stop the projector.")
                QTimer.singleShot(0, self.stop_capture)
            else:
                self.raise_alarm(f"Possible end of reel: {kind} since frame {data['number']}.")

    def _tick(self) -> None:
        s = self.session
        trig_fps = self.trigger_rate.rate() if self.trigger_rate._t and \
            now() - self.trigger_rate._t[-1] < 3.0 else 0.0
        cam_fps = self.camera_rate.rate() if self.camera is not None else 0.0
        warn = check_rate(cam_fps, trig_fps).message if trig_fps else ""
        self.trigger_panel.set_rate(trig_fps, cam_fps, warn)
        if s is None:
            return
        s.tick()
        if s.mode == "capture":
            reel = self.capture_panel.reel
            self.capture_panel.show_stats(s.stats(), reel.gauge if reel else "super8")
            st = s.stats()
            self.preview.overlay_text = (f"REC  {st.saved} saved  {st.dropped} dropped"
                                         if s.state == "capturing" else "PAUSED")
            if warn and s.state == "capturing" and not self._alarm_text:
                self.show_status(warn)
        else:
            self.preview.overlay_text = "DELAY TEST"

    # =============================================================== alarms
    def raise_alarm(self, text: str) -> None:
        self._alarm_text = text
        self.banner.setText(text + "   (click to dismiss)")
        self.banner.setVisible(True)
        QApplication.beep()
        self.alarm_timer.start(1500)
        self.show_status(text)

    def clear_alarm(self) -> None:
        self._alarm_text = ""
        self.banner.setVisible(False)
        self.alarm_timer.stop()

    def show_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ================================================================ misc
    def _open_reel_folder(self) -> None:
        reel = self.capture_panel.reel
        if reel is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(reel.folder))

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "cinecapture_settings.json",
                                              "Settings (*.json)")
        if path:
            self.settings.export_to(path)

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "Settings (*.json)")
        if path:
            self.settings.import_from(path)
            QMessageBox.information(self, "Settings", "Settings imported. Restart the app to "
                                    "apply everything.")

    def _reset_settings(self) -> None:
        if QMessageBox.question(self, "Reset settings", "Reset all settings to their defaults? "
                                "Projects and captured frames are not affected.") == \
                QMessageBox.StandardButton.Yes:
            self.settings.reset()
            QMessageBox.information(self, "Settings", "Settings reset. Restart the app.")

    def _about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {__version__}<br>Cine film capture software.<br>"
                          "Free software under the GNU General Public License v3.0.<br>"
                          "Uses Qt (PySide6, LGPL), OpenCV (Apache 2.0), NumPy (BSD), "
                          "hidapi (BSD) and pyserial (BSD).<br>"
                          "<a href='https://github.com/Treborlee2026/cine-capture-pro'>"
                          "github.com/Treborlee2026/cine-capture-pro</a>")

    def closeEvent(self, e) -> None:
        if self.session is not None and self.session.mode == "capture":
            r = QMessageBox.question(self, "Quit", "A capture is running. Stop it and quit?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.stop_capture()
        elif self.session is not None:
            self.session.stop()
            self.session = None
        if self.trigger is not None:
            self.trigger.stop()
        self.worker.stop()
        self.camera_panel.close_camera(emit=False)
        self.settings.save()
        e.accept()


