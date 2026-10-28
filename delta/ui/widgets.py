"""Small custom widgets shared by the interface."""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QWidget,
)

from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font


def fmt_count(n: int) -> str:
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def clock(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OSError, ValueError):
        return ""


def elide(text: str, fm: QFontMetrics, width: int) -> str:
    return fm.elidedText(text.replace("\n", " "), Qt.TextElideMode.ElideRight, max(20, width))


class IconButton(QWidget):
    clicked = Signal()

    def __init__(
        self,
        icon: str,
        parent=None,
        *,
        size: int = 28,
        icon_size: int = 18,
        tip: str = "",
        color_: str = C.muted,
        hover_color: str = C.ink,
        hover_bg: str = C.raised,
        active_color: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon
        self._size = size
        self._icon_size = icon_size
        self._color = color_
        self._hover_color = hover_color
        self._hover_bg = hover_bg
        self._active_color = active_color or C.brass
        self._hover = False
        self._active = False
        self._down = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if tip:
            self.setToolTip(tip)

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self.update()

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def is_active(self) -> bool:
        return self._active

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._down = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._down = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was = self._down
        self._down = False
        self.update()
        if (
            was
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
            and self.isEnabled()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        enabled = self.isEnabled()
        if self._hover and enabled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(self._hover_bg, 210 if not self._down else 255))
            p.drawRoundedRect(rect, 7, 7)
        if self.hasFocus():
            p.setPen(QPen(color(C.brass, 110), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 7, 7)
        if self._active:
            c = self._active_color
        elif self._hover and enabled:
            c = self._hover_color
        else:
            c = self._color
        if not enabled:
            c = C.faint
        inset = (self._size - self._icon_size) / 2.0
        draw_icon(p, self._icon, QRectF(inset, inset, self._icon_size, self._icon_size), color(c), 1.7)
        p.end()


class PillButton(QPushButton):
    def __init__(self, text: str, parent=None, *, accent: bool = False) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("AccentPill" if accent else "GhostPill")
        if accent:
            self.setStyleSheet(
                f"QPushButton#AccentPill {{ background:{C.brass}; color:#12100B; border:none; border-radius:6px; "
                f"padding:4px 11px; font-size:9pt; font-weight:600; }}"
                f"QPushButton#AccentPill:hover {{ background:{C.brass_hi}; }}"
                f"QPushButton#AccentPill:pressed {{ background:{C.brass_deep}; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton#GhostPill {{ background:transparent; color:{C.ink_dim}; border:1px solid {C.line}; "
                f"border-radius:6px; padding:3px 10px; font-size:9pt; }}"
                f"QPushButton#GhostPill:hover {{ background:{C.raised}; color:{C.ink}; border-color:{C.line_hi}; }}"
                f"QPushButton#GhostPill:pressed {{ background:{C.panel_hi}; }}"
            )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, hspacing: int = 7, vspacing: int = 7) -> None:
        super().__init__(parent)
        self._items = []
        self._h = hspacing
        self._v = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            if hint.width() <= 0 or hint.height() <= 0:
                # QWidgetItem can report 0x0 for a freshly rebuilt child that
                # has not been polished yet.  Ask the widget directly, then
                # fall back to its fixed geometry.
                widget = item.widget()
                if widget is not None:
                    hint = widget.sizeHint()
                    if hint.width() <= 0 or hint.height() <= 0:
                        hint = widget.size()
                if hint.width() <= 0 or hint.height() <= 0:
                    hint = item.minimumSize()
            next_x = x + hint.width() + self._h
            if next_x - self._h > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class Toast(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._text = ""
        self._kind = "normal"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    def show_message(self, text: str, kind: str = "normal", ms: int = 2200) -> None:
        self._text = text
        self._kind = kind
        fm = QFontMetrics(font(FONT_UI, 9.5))
        w = min(max(180, fm.horizontalAdvance(text) + 46), max(320, self.parent().width() - 80))
        self.setFixedSize(w, 38)
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)
        self.update()

    def reposition(self) -> None:
        if not self.parent():
            return
        pw = self.parent().width()
        ph = self.parent().height()
        comp = getattr(self.parent(), "composer", None)
        if comp is not None and comp.isVisible():
            top = comp.mapTo(self.parent(), QPoint(0, 0)).y()
            y = top - self.height() - 14
        else:
            y = ph - 122
        self.move(int((pw - self.width()) / 2), max(12, y))

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._done)
        self._anim.start()

    def _done(self) -> None:
        try:
            self._anim.finished.disconnect(self._done)
        except (RuntimeError, TypeError):
            pass
        self.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(color(C.panel_hi, 246))
        p.setPen(QPen(color(C.line_hi), 1))
        p.drawRoundedRect(rect, 10, 10)
        dot = {
            "normal": C.brass,
            "error": C.danger,
            "ok": C.sage,
        }.get(self._kind, C.brass)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(dot))
        p.drawEllipse(QRectF(16, self.height() / 2 - 2.5, 5, 5))
        p.setPen(color(C.ink_soft))
        p.setFont(font(FONT_UI, 9.5))
        p.drawText(
            QRectF(30, 0, self.width() - 40, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )
        p.end()