"""Dark theme shared by every window."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#e07a1f"        # amber, like a projector lamp
DANGER = "#e5484d"
OK = "#3fb950"
WARN = "#d29922"
BG = "#1b1d21"
PANEL = "#23262b"
FIELD = "#2c3036"
BORDER = "#3a3f46"
TEXT = "#e6e6e6"
MUTED = "#9aa0a6"

STYLE = f"""
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 7px 14px; border: 1px solid {BORDER};
               border-bottom: none; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; border-top: 2px solid {ACCENT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding: 8px;
            background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED};
                   font-weight: 600; }}
QPushButton {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 5px;
              padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #666; }}
QPushButton:checked {{ background: {ACCENT}; color: #111; border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #111; font-weight: 700; border: none;
                      padding: 10px; }}
QPushButton#primary:disabled {{ background: #6b4a2a; color: #999; }}
QPushButton#danger {{ background: {DANGER}; color: white; font-weight: 700; border: none;
                     padding: 10px; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{ background: {FIELD};
    border: 1px solid {BORDER}; border-radius: 4px; padding: 4px; }}
QComboBox QAbstractItemView {{ background: {FIELD}; selection-background-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -6px 0;
                             border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#value {{ font-family: Consolas, monospace; font-size: 11pt; }}
QLabel#big {{ font-family: Consolas, monospace; font-size: 15pt; font-weight: 700; }}
QLabel#banner {{ background: {DANGER}; color: white; font-weight: 700; padding: 8px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QToolBar {{ background: {PANEL}; border: none; spacing: 4px; padding: 3px; }}
QToolButton {{ padding: 4px 8px; border-radius: 4px; }}
QToolButton:checked {{ background: {ACCENT}; color: #111; }}
QScrollArea {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in [
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, FIELD), (QPalette.ColorRole.AlternateBase, PANEL),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, FIELD),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#111111"), (QPalette.ColorRole.ToolTipBase, PANEL),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, MUTED),
    ]:
        pal.setColor(role, QColor(colour))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
