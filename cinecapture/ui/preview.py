"""Live preview widget: zoom, pan, framing guides and overlays."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.gauges import GAUGES
from . import theme


def to_qimage(img: np.ndarray) -> QImage:
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_BGR888).copy()


class PreviewWidget(QWidget):
    zoom_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self._img: Optional[QImage] = None
        self._source_w = 0
        self._zoom = 0.0          # 0 = fit to window; otherwise display px per preview px
        self._center = QPointF(0.5, 0.5)
        self._drag: Optional[QPointF] = None
        self.guides = True
        self.gauge = "super8"
        self.guide_scale = 0.86   # fraction of the image height used by the frame guide
        self.message = "No camera"
        self.overlay_text = ""

    # ------------------------------------------------------------------ data
    def set_image(self, img: np.ndarray, source_width: int) -> None:
        self._img = to_qimage(img)
        self._source_w = source_width
        self.update()

    def clear(self, message: str) -> None:
        self._img = None
        self.message = message
        self.update()

    # ------------------------------------------------------------------ zoom
    def fit(self) -> None:
        self._zoom = 0.0
        self._center = QPointF(0.5, 0.5)
        self.zoom_changed.emit(self.effective_zoom())
        self.update()

    def set_zoom_100(self, at: Optional[QPointF] = None) -> None:
        """Show camera pixels 1:1 (relative to the full-resolution frame)."""
        if self._img is None:
            return
        self._set_zoom(self._source_w / max(1, self._img.width()), at)

    def effective_zoom(self) -> float:
        """Zoom in terms of full-resolution camera pixels per screen pixel (1.0 = 100%)."""
        if self._img is None:
            return 1.0
        scale = self._scale()
        return scale * self._img.width() / max(1, self._source_w)

    def _fit_scale(self) -> float:
        if self._img is None:
            return 1.0
        return min(self.width() / self._img.width(), self.height() / self._img.height())

    def _scale(self) -> float:
        return self._zoom if self._zoom > 0 else self._fit_scale()

    def _set_zoom(self, scale: float, at: Optional[QPointF] = None) -> None:
        if self._img is None:
            return
        fit = self._fit_scale()
        scale = max(fit, min(scale, fit * 64))
        if at is not None:
            before = self._widget_to_norm(at)
            self._zoom = 0.0 if abs(scale - fit) < 1e-6 else scale
            after = self._widget_to_norm(at)
            self._center = QPointF(self._center.x() + before.x() - after.x(),
                                   self._center.y() + before.y() - after.y())
        else:
            self._zoom = 0.0 if abs(scale - fit) < 1e-6 else scale
        self._clamp()
        self.zoom_changed.emit(self.effective_zoom())
        self.update()

    def _clamp(self) -> None:
        if self._img is None or self._zoom <= 0:
            self._center = QPointF(0.5, 0.5)
            return
        s = self._scale()
        half_w = self.width() / (2 * s * self._img.width())
        half_h = self.height() / (2 * s * self._img.height())
        cx = min(max(self._center.x(), half_w), 1 - half_w) if half_w < 0.5 else 0.5
        cy = min(max(self._center.y(), half_h), 1 - half_h) if half_h < 0.5 else 0.5
        self._center = QPointF(cx, cy)

    def _image_rect(self) -> QRectF:
        assert self._img is not None
        s = self._scale()
        w, h = self._img.width() * s, self._img.height() * s
        x = self.width() / 2 - self._center.x() * w
        y = self.height() / 2 - self._center.y() * h
        return QRectF(x, y, w, h)

    def _widget_to_norm(self, p: QPointF) -> QPointF:
        r = self._image_rect()
        return QPointF((p.x() - r.x()) / r.width(), (p.y() - r.y()) / r.height())

    # ---------------------------------------------------------------- events
    def wheelEvent(self, e) -> None:
        if self._img is None:
            return
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self._set_zoom(self._scale() * factor, e.position())

    def mouseDoubleClickEvent(self, e) -> None:
        if self._img is None:
            return
        if self._zoom > 0:
            self.fit()
            return
        full = self._source_w / max(1, self._img.width())    # scale for 100%
        target = full if full > self._fit_scale() * 1.2 else self._fit_scale() * 2.0
        self._set_zoom(target, e.position())

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._zoom > 0:
            self._drag = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and self._img is not None:
            r = self._image_rect()
            d = e.position() - self._drag
            self._center = QPointF(self._center.x() - d.x() / r.width(),
                                   self._center.y() - d.y() / r.height())
            self._drag = e.position()
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        self.unsetCursor()

    def resizeEvent(self, e) -> None:
        self._clamp()
        super().resizeEvent(e)

    # --------------------------------------------------------------- painting
    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0c0e"))
        if self._img is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        r = self._image_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._scale() < 2.0)
        p.drawImage(r, self._img)
        if self.guides:
            self._draw_guides(p, r)
        if self.overlay_text:
            p.setPen(QColor(theme.TEXT))
            f = QFont(p.font())
            f.setPointSize(10)
            p.setFont(f)
            p.drawText(self.rect().adjusted(10, 8, -10, -8),
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.overlay_text)
        z = self.effective_zoom()
        p.setPen(QColor(theme.MUTED))
        p.drawText(self.rect().adjusted(10, 8, -10, -8),
                   Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{z * 100:.0f}%")

    def _draw_guides(self, p: QPainter, r: QRectF) -> None:
        gauge = GAUGES.get(self.gauge)
        if gauge is None:
            return
        gh = r.height() * self.guide_scale
        gw = gh * gauge.aspect
        if gw > r.width() * 0.92:
            gw = r.width() * 0.92
            gh = gw / gauge.aspect
        frame = QRectF(r.center().x() - gw / 2, r.center().y() - gh / 2, gw, gh)
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(frame)
        # thirds
        pen.setColor(QColor(255, 255, 255, 50))
        pen.setStyle(Qt.PenStyle.SolidLine)
        pen.setWidthF(1.0)
        p.setPen(pen)
        for i in (1, 2):
            x = frame.left() + frame.width() * i / 3
            y = frame.top() + frame.height() * i / 3
            p.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            p.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QPointF(frame.left() + 6, frame.top() - 6),
                   f"{gauge.name} frame guide (perforations: {gauge.perf_side})")
