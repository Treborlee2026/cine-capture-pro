"""Capture tab: project and reel, file format, start/stop and live counters."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core.gauges import GAUGES, footage, format_duration, running_time
from ..core.project import Project, Reel
from ..core.session import SessionStats
from ..core.writer import FORMATS


class ReelDialog(QDialog):
    """New reel / edit reel details."""

    def __init__(self, parent=None, reel: Optional[Reel] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reel details")
        form = QFormLayout(self)
        self.title = QLineEdit(reel.metadata.title if reel else "")
        self.title.setPlaceholderText("e.g. Holiday 1965")
        self.gauge = QComboBox()
        for key, g in GAUGES.items():
            self.gauge.addItem(g.name, key)
        if reel:
            self.gauge.setCurrentIndex(max(0, self.gauge.findData(reel.gauge)))
        else:
            self.gauge.setCurrentIndex(self.gauge.findData("super8"))
        self.stock_type = QComboBox()
        self.stock_type.addItem("Positive (reversal or print)", False)
        self.stock_type.addItem("Negative", True)
        if reel and reel.negative:
            self.stock_type.setCurrentIndex(1)
        m = reel.metadata if reel else None
        self.stock = QLineEdit(m.stock if m else "")
        self.stock.setPlaceholderText("e.g. Kodachrome 40")
        self.date = QLineEdit(m.shoot_date if m else "")
        self.location = QLineEdit(m.location if m else "")
        self.edge = QLineEdit(m.edge_code if m else "")
        self.notes = QPlainTextEdit(m.notes if m else "")
        self.notes.setFixedHeight(70)
        form.addRow("Title", self.title)
        form.addRow("Gauge", self.gauge)
        form.addRow("Film type", self.stock_type)
        form.addRow("Film stock", self.stock)
        form.addRow("Shoot date", self.date)
        form.addRow("Location", self.location)
        form.addRow("Edge code", self.edge)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _ok(self) -> None:
        if not self.title.text().strip():
            self.title.setFocus()
            return
        self.accept()

    def apply_to(self, reel: Reel) -> None:
        reel.metadata.title = self.title.text().strip()
        reel.gauge = self.gauge.currentData()
        reel.negative = bool(self.stock_type.currentData())
        reel.metadata.stock = self.stock.text().strip()
        reel.metadata.shoot_date = self.date.text().strip()
        reel.metadata.location = self.location.text().strip()
        reel.metadata.edge_code = self.edge.text().strip()
        reel.metadata.notes = self.notes.toPlainText().strip()


class CapturePanel(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    pause_requested = Signal(bool)
    reel_changed = Signal(object)        # Reel or None
    status = Signal(str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.project: Optional[Project] = None
        self.reel: Optional[Reel] = None
        root = QVBoxLayout(self)

        pbox = QGroupBox("Project")
        p = QVBoxLayout(pbox)
        self.project_label = QLabel("No project open")
        self.project_label.setWordWrap(True)
        p.addWidget(self.project_label)
        row = QHBoxLayout()
        new_p = QPushButton("New project...")
        new_p.clicked.connect(self.new_project)
        open_p = QPushButton("Open...")
        open_p.clicked.connect(self.open_project)
        row.addWidget(new_p)
        row.addWidget(open_p)
        p.addLayout(row)
        p.addWidget(QLabel("Reel"))
        row = QHBoxLayout()
        self.reel_combo = QComboBox()
        self.reel_combo.currentIndexChanged.connect(lambda _: self._reel_selected())
        self.new_reel_btn = QPushButton("New reel...")
        self.new_reel_btn.clicked.connect(self.new_reel)
        self.edit_reel_btn = QPushButton("Details...")
        self.edit_reel_btn.clicked.connect(self.edit_reel)
        row.addWidget(self.reel_combo, 1)
        row.addWidget(self.new_reel_btn)
        row.addWidget(self.edit_reel_btn)
        p.addLayout(row)
        self.reel_info = QLabel("")
        self.reel_info.setObjectName("muted")
        self.reel_info.setWordWrap(True)
        p.addWidget(self.reel_info)
        root.addWidget(pbox)

        obox = QGroupBox("Saving")
        o = QFormLayout(obox)
        self.fmt = QComboBox()
        for key, (_ext, desc) in FORMATS.items():
            self.fmt.addItem(desc, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(settings["file_format"])))
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        o.addRow("Format", self.fmt)
        self.start_frame = QSpinBox()
        self.start_frame.setRange(1, 9_999_999)
        self.start_frame.setToolTip("Number of the next saved frame. Continues automatically "
                                    "after a previous capture of this reel.")
        o.addRow("Next frame number", self.start_frame)
        self.eor = QCheckBox("Stop automatically at the end of the reel")
        self.eor.setChecked(bool(settings["auto_stop_at_end"]))
        self.eor.toggled.connect(self._eor_changed)
        o.addRow(self.eor)
        self.show_captured = QCheckBox("Preview shows each captured frame")
        self.show_captured.setChecked(bool(settings["view"].get("show_captured")))
        o.addRow(self.show_captured)
        root.addWidget(obox)

        self.start_btn = QPushButton("Start capture")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self.pause_requested.emit)
        row = QHBoxLayout()
        row.addWidget(self.start_btn, 2)
        row.addWidget(self.pause_btn, 1)
        row.addWidget(self.stop_btn, 1)
        root.addLayout(row)

        sbox = QGroupBox("This capture")
        g = QGridLayout(sbox)
        self.vals = {}
        items = [("triggers", "Triggers"), ("saved", "Frames saved"), ("dropped", "Dropped"),
                 ("duplicates", "Duplicates"), ("rate", "Capture speed"),
                 ("latency", "Trigger to frame"), ("queue", "Write buffer"),
                 ("footage", "Footage"), ("runtime", "Running time"),
                 ("elapsed", "Elapsed"), ("disk", "Disk free"), ("last", "Last frame")]
        for i, (key, label) in enumerate(items):
            lab = QLabel(label)
            lab.setObjectName("muted")
            val = QLabel("-")
            val.setObjectName("big" if key in ("saved", "dropped") else "value")
            g.addWidget(lab, (i // 2) * 2, i % 2)
            g.addWidget(val, (i // 2) * 2 + 1, i % 2)
            self.vals[key] = val
        root.addWidget(sbox)
        root.addStretch()
        self.set_state("idle")
        self._refresh_project()

    # --------------------------------------------------------------- project
    def new_project(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "Choose the capture drive or folder "
                                                  "for the new project")
        if not parent:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("New project")
        f = QFormLayout(dlg)
        name = QLineEdit()
        name.setPlaceholderText("e.g. Family Films 1965")
        f.addRow("Project name", name)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted or not name.text().strip():
            return
        try:
            self.project = Project.create(parent, name.text().strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "New project", str(exc))
            return
        self._remember()
        self._refresh_project()
        self.new_reel()

    def open_project(self, path: str = "") -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", "",
                                                  "Cine Capture project (project.ccp)")
            if not path:
                return
        try:
            self.project = Project.open(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Open project", f"Could not open the project: {exc}")
            return
        self._remember()
        self._refresh_project()

    def _remember(self) -> None:
        if self.project is None:
            return
        self.settings["last_project"] = self.project.folder
        recent = [p for p in self.settings["recent_projects"] if p != self.project.folder]
        self.settings["recent_projects"] = [self.project.folder] + recent[:9]
        self.settings.save()

    def _refresh_project(self) -> None:
        self.reel_combo.blockSignals(True)
        self.reel_combo.clear()
        if self.project is None:
            self.project_label.setText("No project open. Create one on your capture drive.")
        else:
            self.project_label.setText(f"<b>{self.project.name}</b><br>{self.project.folder}")
            for r in self.project.reels:
                self.reel_combo.addItem(f"{r.id}  {r.metadata.title}", r.id)
            if self.project.reels:
                self.reel_combo.setCurrentIndex(self.reel_combo.count() - 1)
        self.reel_combo.blockSignals(False)
        self.new_reel_btn.setEnabled(self.project is not None)
        self._reel_selected()

    def new_reel(self) -> None:
        if self.project is None:
            return
        dlg = ReelDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        reel = self.project.add_reel(dlg.title.text().strip(), gauge=dlg.gauge.currentData(),
                                     file_format=self.fmt.currentData())
        dlg.apply_to(reel)
        reel.save()
        self._refresh_project()

    def edit_reel(self) -> None:
        if self.reel is None:
            return
        dlg = ReelDialog(self, self.reel)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to(self.reel)
            self.reel.save()
            self._refresh_project()

    def _reel_selected(self) -> None:
        rid = self.reel_combo.currentData()
        self.reel = self.project.reel(rid) if (self.project and rid) else None
        self.edit_reel_btn.setEnabled(self.reel is not None)
        if self.reel is not None:
            r = self.reel
            g = GAUGES[r.gauge]
            self.reel_info.setText(
                f"{g.name}, {'negative' if r.negative else 'positive'}"
                f"{', ' + r.metadata.stock if r.metadata.stock else ''}. "
                f"{r.frames_captured} frames captured so far "
                f"({footage(r.frames_captured, g):.0f} ft).")
            self.start_frame.setValue(r.next_frame)
            idx = self.fmt.findData(r.file_format)
            if idx >= 0 and r.frames_captured:
                self.fmt.setCurrentIndex(idx)
        else:
            self.reel_info.setText("")
        self.reel_changed.emit(self.reel)
        self.set_state("idle")

    def _fmt_changed(self) -> None:
        self.settings["file_format"] = self.fmt.currentData()
        self.settings.save()

    def _eor_changed(self, on: bool) -> None:
        self.settings["auto_stop_at_end"] = on
        self.settings.save()

    # ----------------------------------------------------------------- state
    def set_state(self, state: str, ready: bool = True) -> None:
        capturing = state in ("capturing", "paused")
        self.start_btn.setEnabled(not capturing and self.reel is not None and ready)
        self.stop_btn.setEnabled(capturing)
        self.pause_btn.setEnabled(capturing)
        if not capturing:
            self.pause_btn.blockSignals(True)
            self.pause_btn.setChecked(False)
            self.pause_btn.blockSignals(False)
        self.pause_btn.setText("Resume" if state == "paused" else "Pause")
        for w in (self.reel_combo, self.new_reel_btn, self.edit_reel_btn, self.fmt,
                  self.start_frame):
            w.setEnabled(not capturing and (w is not self.new_reel_btn or self.project is not None))
        self.start_btn.setText("Capturing..." if state == "capturing" else (
            "Paused" if state == "paused" else "Start capture"))

    def show_stats(self, s: SessionStats, gauge_key: str) -> None:
        g = GAUGES.get(gauge_key, GAUGES["super8"])
        v = self.vals
        v["triggers"].setText(f"{s.triggers}")
        v["saved"].setText(f"{s.saved}")
        v["dropped"].setText(f"{s.dropped}")
        v["dropped"].setStyleSheet("color: #e5484d;" if s.dropped else "")
        v["duplicates"].setText(f"{s.duplicates}")
        v["duplicates"].setStyleSheet("color: #d29922;" if s.duplicates else "")
        v["rate"].setText(f"{s.trigger_rate:.2f} fps")
        v["latency"].setText(f"{s.mean_latency_ms:.0f} ms")
        v["queue"].setText(f"{s.queued_frames} ({s.queue_fraction * 100:.0f}%)")
        v["queue"].setStyleSheet("color: #e5484d;" if s.queue_fraction > 0.5 else "")
        v["footage"].setText(f"{footage(s.saved, g):.1f} ft")
        v["runtime"].setText(f"{format_duration(running_time(s.saved, g.default_fps))} at "
                             f"{g.default_fps:g} fps")
        v["elapsed"].setText(format_duration(s.elapsed))
        v["last"].setText(f"{s.last_number}" if s.last_number else "-")
        if self.project is not None:
            try:
                import shutil
                free = shutil.disk_usage(self.project.folder).free
                per = (s.bytes_written / s.saved) if s.saved else 0
                left = f", ~{free / per / max(s.trigger_rate, 0.01) / 60:.0f} min" if per and \
                    s.trigger_rate > 0 else ""
                v["disk"].setText(f"{free / 1e9:.0f} GB{left}")
                v["disk"].setStyleSheet("color: #e5484d;" if free < 5e9 else "")
            except OSError:
                v["disk"].setText("-")

    def reopen_last(self) -> None:
        last = self.settings.get("last_project")
        if last and os.path.exists(os.path.join(last, "project.ccp")):
            self.open_project(last)
