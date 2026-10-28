"""Hand-drawn 24x24 icons.  No icon font, no theme lookup, no emoji."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

from .theme import C


def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.55) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(rect.center())
    scale = min(rect.width(), rect.height()) / 24.0
    p.scale(scale, scale)
    p.translate(-12, -12)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "minus":
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "send":
        p.drawLine(QPointF(12, 19), QPointF(12, 5))
        p.drawLine(QPointF(6, 11), QPointF(12, 5))
        p.drawLine(QPointF(18, 11), QPointF(12, 5))
    elif name == "stop":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(8, 8, 8, 8), 2.2, 2.2)
    elif name == "copy":
        p.drawRoundedRect(QRectF(8.5, 9, 10, 11), 2, 2)
        path = QPainterPath()
        path.moveTo(6, 15.5)
        path.cubicTo(4.6, 15.3, 4, 14.5, 4, 13.2)
        path.lineTo(4, 6.8)
        path.cubicTo(4, 5.2, 5.2, 4, 6.8, 4)
        path.lineTo(12.6, 4)
        path.cubicTo(14.1, 4, 15, 4.8, 15, 6.2)
        p.drawPath(path)
    elif name == "fork":
        p.drawLine(QPointF(8, 4), QPointF(8, 20))
        p.drawEllipse(QPointF(8, 4), 2.0, 2.0)
        p.drawEllipse(QPointF(8, 20), 2.0, 2.0)
        path = QPainterPath()
        path.moveTo(8, 10)
        path.cubicTo(13.5, 10, 16, 12.5, 16, 16)
        path.lineTo(16, 20)
        p.drawPath(path)
        p.drawEllipse(QPointF(16, 20), 2.0, 2.0)
    elif name == "trash":
        p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
        p.drawLine(QPointF(9, 4.5), QPointF(15, 4.5))
        p.drawRoundedRect(QRectF(6.5, 7, 11, 12.5), 1.8, 1.8)
        p.drawLine(QPointF(10, 10.5), QPointF(10, 16.5))
        p.drawLine(QPointF(14, 10.5), QPointF(14, 16.5))
    elif name == "pin":
        path = QPainterPath()
        path.moveTo(12, 20)
        path.cubicTo(12, 20, 5.5, 14.2, 5.5, 9.4)
        path.cubicTo(5.5, 5.6, 8.4, 3.5, 12, 3.5)
        path.cubicTo(15.6, 3.5, 18.5, 5.6, 18.5, 9.4)
        path.cubicTo(18.5, 14.2, 12, 20, 12, 20)
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 9.4), 2.3, 2.3)
    elif name == "pin_fill":
        path = QPainterPath()
        path.moveTo(12, 20)
        path.cubicTo(12, 20, 5.5, 14.2, 5.5, 9.4)
        path.cubicTo(5.5, 5.6, 8.4, 3.5, 12, 3.5)
        path.cubicTo(15.6, 3.5, 18.5, 5.6, 18.5, 9.4)
        path.cubicTo(18.5, 14.2, 12, 20, 12, 20)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
    elif name == "map":
        p.drawLine(QPointF(12, 6), QPointF(12, 10))
        p.drawLine(QPointF(12, 10), QPointF(6, 16))
        p.drawLine(QPointF(12, 10), QPointF(18, 16))
        p.drawEllipse(QPointF(12, 5), 2.2, 2.2)
        p.drawEllipse(QPointF(6, 18), 2.2, 2.2)
        p.drawEllipse(QPointF(18, 18), 2.2, 2.2)
    elif name == "library":
        p.drawRoundedRect(QRectF(4.5, 7, 12, 12.5), 2, 2)
        p.drawLine(QPointF(8, 4.5), QPointF(18.5, 4.5))
        p.drawLine(QPointF(18.5, 4.5), QPointF(18.5, 15))
    elif name == "settings":
        for y, knob in ((6.5, 15.0), (12.0, 8.5), (17.5, 14.0)):
            p.drawLine(QPointF(4.5, y), QPointF(19.5, y))
            p.setBrush(QColor(C.bg_soft))
            p.drawEllipse(QPointF(knob, y), 2.0, 2.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "close":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
    elif name == "check":
        path = QPainterPath()
        path.moveTo(5, 12.5)
        path.lineTo(9.8, 17.2)
        path.lineTo(19, 7)
        p.drawPath(path)
    elif name == "warning":
        path = QPainterPath()
        path.moveTo(12, 4)
        path.lineTo(21, 19.5)
        path.lineTo(3, 19.5)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 9.5), QPointF(12, 14))
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 16.8), 0.9, 0.9)
    elif name == "refresh":
        p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 280 * 16)
        path = QPainterPath()
        path.moveTo(16.2, 3.5)
        path.lineTo(19.4, 6.7)
        path.lineTo(15.2, 8.4)
        p.drawPath(path)
    elif name == "chevron_down":
        p.drawLine(QPointF(7, 9.5), QPointF(12, 14.5))
        p.drawLine(QPointF(12, 14.5), QPointF(17, 9.5))
    elif name == "chevron_right":
        p.drawLine(QPointF(9.5, 7), QPointF(14.5, 12))
        p.drawLine(QPointF(14.5, 12), QPointF(9.5, 17))
    elif name == "chevron_left":
        p.drawLine(QPointF(14.5, 7), QPointF(9.5, 12))
        p.drawLine(QPointF(9.5, 12), QPointF(14.5, 17))
    elif name == "more":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        for x in (6.5, 12, 17.5):
            p.drawEllipse(QPointF(x, 12), 1.3, 1.3)
    elif name == "edit":
        p.drawLine(QPointF(5, 19), QPointF(6, 15))
        p.drawLine(QPointF(6, 15), QPointF(15.5, 5.5))
        p.drawLine(QPointF(15.5, 5.5), QPointF(18.5, 8.5))
        p.drawLine(QPointF(18.5, 8.5), QPointF(9, 18))
        p.drawLine(QPointF(9, 18), QPointF(5, 19))
    elif name == "arrow_left":
        p.drawLine(QPointF(19, 12), QPointF(5, 12))
        p.drawLine(QPointF(5, 12), QPointF(11, 6))
        p.drawLine(QPointF(5, 12), QPointF(11, 18))
    elif name == "arrow_down":
        p.drawLine(QPointF(12, 4), QPointF(12, 20))
        p.drawLine(QPointF(6, 14), QPointF(12, 20))
        p.drawLine(QPointF(18, 14), QPointF(12, 20))
    elif name == "spark":
        path = QPainterPath()
        path.moveTo(12, 3.5)
        path.cubicTo(12.8, 9, 15, 11.2, 20.5, 12)
        path.cubicTo(15, 12.8, 12.8, 15, 12, 20.5)
        path.cubicTo(11.2, 15, 9, 12.8, 3.5, 12)
        path.cubicTo(9, 11.2, 11.2, 9, 12, 3.5)
        p.drawPath(path)
    p.restore()


def icon_pixmap(name: str, size: int, color: QColor) -> QPixmap:
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, name, QRectF(0, 0, size, size), color, 1.55)
    p.end()
    return pm


def draw_logo(p: QPainter, rect: QRectF, color: QColor, fill: bool = False) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(rect.center())
    s = min(rect.width(), rect.height())
    p.scale(s / 28.0, s / 28.0)
    path = QPainterPath()
    path.moveTo(0, -11)
    path.lineTo(10.5, 9.5)
    path.lineTo(-10.5, 9.5)
    path.closeSubpath()
    pen = QPen(color, 1.7)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if fill:
        p.setBrush(color)
    p.drawPath(path)
    if not fill:
        p.setPen(QPen(color, 0.0))
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 3.4), 1.15, 1.15)
    p.restore()
