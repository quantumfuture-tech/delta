"""A non-persistent error strip for failures with no message to attach to."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .icons import draw_icon
from .theme import C, FONT_UI, color, font
from .widgets import IconButton, PillButton


class NoticeBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMaximumWidth(890)
        self.setFixedHeight(54)
        self._action = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 8, 8)
        lay.setSpacing(10)
        self._icon = _WarningMark(self)
        self._icon.setFixedSize(20, 20)
        self._title = QLabel("", self)
        self._title.setFont(font(FONT_UI, 9.4, 600))
        self._title.setStyleSheet(f"color:{C.danger}; background:transparent;")
        self._detail = QLabel("", self)
        self._detail.setFont(font(FONT_UI, 9.0))
        self._detail.setStyleSheet(f"color:{C.muted}; background:transparent;")
        self._action_btn = PillButton("重试", self)
        self._action_btn.clicked.connect(self._run_action)
        self._close = IconButton("close", self, size=26, icon_size=13, tip="关闭", color_=C.muted)
        self._close.clicked.connect(self.hide)
        lay.addWidget(self._icon)
        lay.addWidget(self._title)
        lay.addWidget(self._detail, 1)
        lay.addWidget(self._action_btn)
        lay.addWidget(self._close)
        self.hide()

    def show_notice(self, title: str, detail: str, action: str | None = None, callback=None) -> None:
        self._title.setText(title)
        self._detail.setText(detail)
        self._action = callback
        self._action_btn.setVisible(bool(action))
        if action:
            self._action_btn.setText(action)
        self.show()

    def _run_action(self) -> None:
        if self._action:
            self._action()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(color("#43261F"), 1))
        p.setBrush(color("#1A1311"))
        p.drawRoundedRect(r, 11, 11)
        p.end()

class _WarningMark(QWidget):
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.danger_dim))
        p.drawEllipse(QRectF(0, 0, 20, 20))
        draw_icon(p, "warning", QRectF(4, 4, 12, 12), color(C.danger), 1.4)
        p.end()
