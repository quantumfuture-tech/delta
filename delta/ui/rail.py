"""The 64px rail: orientation, creation, navigation.  Nothing else."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .icons import draw_logo
from .theme import C, FONT_MONO, color, font
from .widgets import IconButton


class LogoMark(QWidget):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover = False
        self.setFixedSize(64, 58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("Delta · 会分叉的对话")

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.raised, 180))
            p.drawRoundedRect(QRectF(15, 12, 34, 34), 10, 10)
        draw_logo(p, QRectF(20, 16, 24, 24), color(C.brass), False)
        p.end()


class Rail(QWidget):
    new_requested = Signal()
    sessions_requested = Signal()
    atlas_toggled = Signal(bool)
    settings_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(64)
        self._atlas_on = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 12)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.logo = LogoMark(self)
        lay.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(12)
        self._line()

        self.btn_new = IconButton("plus", self, size=40, icon_size=20, tip="新对话  (Ctrl+N)", color_=C.ink_dim, hover_color=C.ink)
        self.btn_sessions = IconButton("library", self, size=40, icon_size=19, tip="对话库  (Ctrl+L)", color_=C.ink_dim, hover_color=C.ink)
        self.btn_atlas = IconButton("map", self, size=40, icon_size=19, tip="显示/隐藏 ATLAS  (Ctrl+M)", color_=C.ink_dim, hover_color=C.ink)
        self.btn_atlas.set_active(True)
        lay.addWidget(self.btn_new, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.btn_sessions, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.btn_atlas, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self._line()
        self.btn_settings = IconButton("settings", self, size=38, icon_size=18, tip="关于与设置", color_=C.muted)
        lay.addWidget(self.btn_settings, 0, Qt.AlignmentFlag.AlignHCenter)

        self.logo.clicked.connect(self.new_requested)
        self.btn_new.clicked.connect(self.new_requested)
        self.btn_sessions.clicked.connect(self.sessions_requested)
        self.btn_atlas.clicked.connect(self._toggle_atlas)
        self.btn_settings.clicked.connect(self.settings_requested)

    def _line(self) -> None:
        holder = QHBoxLayout()
        holder.setContentsMargins(0, 6, 0, 6)
        line = QWidget(self)
        line.setFixedSize(22, 1)
        line.setStyleSheet(f"background:{C.line_soft};")
        holder.addStretch(1)
        holder.addWidget(line)
        holder.addStretch(1)
        self.layout().addLayout(holder)

    def _toggle_atlas(self) -> None:
        self._atlas_on = not self._atlas_on
        self.btn_atlas.set_active(self._atlas_on)
        self.atlas_toggled.emit(self._atlas_on)

    def set_atlas(self, on: bool) -> None:
        self._atlas_on = on
        self.btn_atlas.set_active(on)