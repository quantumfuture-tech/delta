"""First-run surface: an invitation, not a blank chat box."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .icons import draw_logo
from .theme import C, FONT_CJK, FONT_MONO, FONT_UI, color, font


class SuggestionRow(QWidget):
    chosen = Signal(str)

    def __init__(self, index: int, text: str, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.text = text
        self._hover = False
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMaximumWidth(620)
        self.setMinimumWidth(380)

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.chosen.emit(self.text)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(color(C.line_hi if self._hover else C.line), 1))
        p.setBrush(color(C.raised if self._hover else C.panel))
        p.drawRoundedRect(r, 10, 10)
        p.setFont(font(FONT_MONO, 8.0, 500, 0.8))
        p.setPen(color(C.brass if self._hover else C.brass_dim))
        p.drawText(QRectF(18, 0, 26, self.height()), Qt.AlignmentFlag.AlignVCenter, f"{self.index:02d}")
        p.setFont(font(FONT_UI, 10.2))
        p.setPen(color(C.ink if self._hover else C.ink_soft))
        p.drawText(QRectF(52, 0, self.width() - 82, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)
        p.setPen(QPen(color(C.brass if self._hover else C.muted), 1.4))
        p.drawLine(self.width() - 31, self.height() / 2, self.width() - 22, self.height() / 2)
        p.drawLine(self.width() - 27, self.height() / 2 - 5, self.width() - 22, self.height() / 2)
        p.drawLine(self.width() - 27, self.height() / 2 + 5, self.width() - 22, self.height() / 2)
        p.end()


class EmptyState(QWidget):
    suggestion = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 30, 60, 20)
        root.addStretch(1)

        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)
        wrap.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        brand = QLabel("DELTA   ·   会分叉的对话", self)
        brand.setFont(font(FONT_MONO, 8.4, 500, 1.5))
        brand.setStyleSheet(f"color:{C.brass_dim}; background:transparent;")
        brand.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        wrap.addWidget(brand)
        wrap.addSpacing(20)

        title = QLabel("从一个问题开始。", self)
        title.setFont(font(FONT_CJK, 26, 300))
        title.setStyleSheet(f"color:{C.ink}; background:transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        wrap.addWidget(title)
        wrap.addSpacing(12)

        sub = QLabel("对话不是一条线。任何一句都能再开一支，旧的不会丢。", self)
        sub.setFont(font(FONT_UI, 10.5))
        sub.setStyleSheet(f"color:{C.ink_dim}; background:transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        wrap.addWidget(sub)
        wrap.addSpacing(34)

        self.rows: list[SuggestionRow] = []
        texts = [
            "把这个问题拆成三条互相矛盾的路。",
            "给我一个反例，再给出你自己的结论。",
            "先别回答，先问我三个关键问题。",
        ]
        for i, text in enumerate(texts):
            row = SuggestionRow(i + 1, text, self)
            row.chosen.connect(self.suggestion)
            wrap.addWidget(row, 0, Qt.AlignmentFlag.AlignHCenter)
            wrap.addSpacing(8)
            self.rows.append(row)

        wrap.addSpacing(10)
        hint = QLabel("写下来按 Enter 发送  ·  Shift+Enter 换行", self)
        hint.setFont(font(FONT_UI, 8.6))
        hint.setStyleSheet(f"color:{C.faint}; background:transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        wrap.addWidget(hint)
        root.addLayout(wrap)
        root.addStretch(1)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # A faint topology in the corner: the product's shape as wallpaper.
        base = QRectF(self.width() - 380, 40, 330, 330)
        p.setPen(QPen(color(C.brass, 14), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(base)
        p.setPen(QPen(color(C.brass, 10), 1))
        p.drawEllipse(base.adjusted(42, 42, -42, -42))
        p.drawEllipse(base.adjusted(88, 88, -88, -88))
        path = QPainterPath()
        cx, cy = base.center().x(), base.center().y()
        path.moveTo(cx, cy - 165)
        path.lineTo(cx, cy + 165)
        path.moveTo(cx, cy - 40)
        path.cubicTo(cx + 80, cy - 20, cx + 40, cy + 80, cx + 140, cy + 120)
        path.moveTo(cx, cy + 30)
        path.cubicTo(cx - 80, cy + 40, cx - 50, cy + 120, cx - 120, cy + 150)
        p.setPen(QPen(color(C.brass, 12), 1))
        p.drawPath(path)
        p.setPen(QPen(color(C.brass, 30), 1))
        for point in ((cx, cy - 165), (cx, cy + 165), (cx + 140, cy + 120), (cx - 120, cy + 150)):
            p.drawEllipse(QRectF(point[0] - 3, point[1] - 3, 6, 6))
        draw_logo(p, QRectF(self.width() - 146, self.height() - 150, 48, 48), color(C.brass, 16), False)
        p.end()
