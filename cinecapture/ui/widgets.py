"""Small reusable widgets: histogram, focus loupe, control slider, LED, test strip."""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSizePolicy,
                               QSlider, QVBoxLayout, QWidget)

from . import theme
from .preview import to_qimage


class HistogramWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(90)
        self._hist: Optional[np.ndarray] = None
        self.clipped = 0.0

    def set_histogram(self, hist: Optional[np.ndarray], clipped: float) -> None:
        self._hist = hist
        self.clipped = clipped
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(r, QColor("#111316"))
        p.setPen(QColor(theme.BORDER))
        p.drawRect(r)
        if self._hist is None:
            return
        colours = [QColor(80, 140, 255, 150), QColor(80, 220, 120, 150),
                   QColor(255, 90, 90, 150)] if len(self._hist) == 3 else [QColor(220, 220, 220)]
        w, h = r.width(), r.height() - 4
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for chan, col in zip(self._hist, colours):
            path = QPainterPath(QPointF(0, r.bottom()))
            n = len(chan)
            for i, v in enumerate(chan):
                path.lineTo(QPointF(i * w / (n - 1), r.bottom() - float(v) * h))
            path.lineTo(QPointF(w, r.bottom()))
            path.closeSubpath()
            p.fillPath(path, col)
        if self.clipped > 0.0005:
            p.fillRect(QRectF(r.right() - 4, r.top(), 4, r.height()), QColor(theme.DANGER))
            p.setPen(QColor(theme.DANGER))
            p.drawText(r.adjusted(4, 2, -8, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
                       f"clipped {self.clipped * 100:.1f}%")


class ImageLabel(QWidget):
    """Shows a numpy BGR image scaled to fit."""

    clicked = Signal()

    def __init__(self, min_w: int = 120, min_h: int = 90, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(min_w, min_h)
        self._img: Optional[QImage] = None
        self.caption = ""
        self.caption_colour = QColor(theme.TEXT)
        self.border: Optional[QColor] = None

    def set_image(self, img: Optional[np.ndarray]) -> None:
        self._img = to_qimage(img) if img is not None else None
        self.update()

    def mousePressEvent(self, e) -> None:
        self.clicked.emit()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0c0e"))
        if self._img is not None:
            s = min(self.width() / self._img.width(), self.height() / self._img.height())
            w, h = self._img.width() * s, self._img.height() * s
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self._img)
        if self.border is not None:
            pen = QPen(self.border)
            pen.setWidth(3)
            p.setPen(pen)
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self.caption:
            p.fillRect(QRectF(0, self.height() - 20, self.width(), 20), QColor(0, 0, 0, 170))
            p.setPen(self.caption_colour)
            p.drawText(QRectF(4, self.height() - 20, self.width() - 8, 20),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.caption)


class FocusLoupe(QWidget):
    """A 1:1 crop of the full-resolution frame with a sharpness meter and peak hold."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = ImageLabel(200, 150)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.value = QLabel("Sharpness: -")
        self.value.setObjectName("value")
        self.peak = QLabel("Peak: -")
        self.peak.setObjectName("muted")
        row.addWidget(self.value)
        row.addStretch()
        row.addWidget(self.peak)
        lay.addLayout(row)
        self.bar = _Bar()
        lay.addWidget(self.bar)
        self._peak = 0.0

    def reset_peak(self) -> None:
        self._peak = 0.0

    def update_crop(self, crop: np.ndarray, score: float) -> None:
        self.view.set_image(crop)
        self._peak = max(self._peak, score)
        self.value.setText(f"Sharpness: {score:,.0f}")
        self.peak.setText(f"Peak: {self._peak:,.0f}")
        self.bar.set(score / self._peak if self._peak else 0.0)


class _Bar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(10)
        self._v = 0.0

    def set(self, v: float) -> None:
        self._v = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.FIELD))
        col = QColor(theme.OK) if self._v > 0.95 else QColor(theme.ACCENT)
        p.fillRect(QRectF(0, 0, self.width() * self._v, self.height()), col)


class ControlRow(QWidget):
    """Label + slider + number + optional Auto tick box for one camera control."""

    value_changed = Signal(str, float)
    auto_changed = Signal(str, bool)

    def __init__(self, key: str, name: str, lo: float, hi: float, step: float, value: float,
                 has_auto: bool, auto: bool, unit: str = "", note: str = "", parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self._step = step if step > 0 else 1.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        top = QHBoxLayout()
        label = QLabel(name + (f" ({unit})" if unit else ""))
        if note:
            label.setToolTip(note)
        top.addWidget(label)
        top.addStretch()
        self.auto = QCheckBox("Auto")
        self.auto.setVisible(has_auto)
        self.auto.setChecked(auto)
        self.auto.toggled.connect(self._on_auto)
        top.addWidget(self.auto)
        lay.addLayout(top)
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo / self._step)), int(round(hi / self._step)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(-100000, 100000)       # drivers may accept values outside the slider
        self.spin.setDecimals(0 if self._step >= 1 else 2)
        self.spin.setSingleStep(self._step)
        self.spin.setFixedWidth(90)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        lay.addLayout(row)
        self._set(value)
        self.slider.valueChanged.connect(lambda v: self._from_slider(v))
        self.spin.editingFinished.connect(lambda: self._emit(self.spin.value()))
        self._apply_auto_state(auto)

    def _set(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(int(round(value / self._step)))
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)

    def set_value(self, value: float) -> None:
        self._set(value)

    def _from_slider(self, v: int) -> None:
        value = v * self._step
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self._emit(value)

    def _emit(self, value: float) -> None:
        self._set(value)
        self.value_changed.emit(self.key, float(value))

    def _on_auto(self, on: bool) -> None:
        self._apply_auto_state(on)
        self.auto_changed.emit(self.key, on)

    def _apply_auto_state(self, auto: bool) -> None:
        self.slider.setEnabled(not auto)
        self.spin.setEnabled(not auto)

    def set_locked(self, locked: bool) -> None:
        self.slider.setEnabled(not locked and not self.auto.isChecked())
        self.spin.setEnabled(not locked and not self.auto.isChecked())
        self.auto.setEnabled(not locked)


class Led(QWidget):
    """A dot that flashes when a trigger pulse arrives."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._on = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._off)

    def flash(self) -> None:
        self._on = True
        self.update()
        self._timer.start(90)

    def _off(self) -> None:
        self._on = False
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(theme.OK) if self._on else QColor("#333"))
        p.setPen(QColor(theme.BORDER))
        p.drawEllipse(2, 2, 12, 12)


class TestStrip(QWidget):
    """The last eight test-mode frames side by side, sharpest marked green."""

    def __init__(self, count: int = 8, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.cells: List[ImageLabel] = []
        for _ in range(count):
            c = ImageLabel(90, 68)
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.cells.append(c)
            lay.addWidget(c)

    def set_items(self, items: list) -> None:
        """items: list of dicts with 'preview', 'sharpness', 'number' (oldest first)."""
        scores = [i.get("sharpness") or 0.0 for i in items]
        best = max(scores) if scores else 0.0
        for cell, item in zip(self.cells, list(items) + [None] * len(self.cells)):
            if item is None:
                cell.set_image(None)
                cell.caption = ""
                cell.border = None
                continue
            cell.set_image(item.get("preview"))
            score = item.get("sharpness") or 0.0
            rel = score / best if best else 0
            cell.caption = f"#{item['number']}  {rel * 100:.0f}%"
            cell.border = QColor(theme.OK) if rel >= 0.9 else (
                QColor(theme.WARN) if rel >= 0.7 else QColor(theme.DANGER))
            cell.update()
