"""Atlas: the whole tree, fitted into a quiet panel.

The transcript shows one path; this is the only place where the shape of the
whole conversation is visible at once.  Nodes are dots, forks are diamonds, the
view is brass.  Clicking a node sets the baseline there; the lock set follows.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.model import Conversation, one_line
from .icons import draw_logo
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import fmt_count


class AtlasView(QWidget):
    node_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.convo: Conversation | None = None
        self._pos: dict[str, tuple[float, float]] = {}
        self._pix: dict[str, QPointF] = {}
        self._hover: str | None = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.setMouseTracking(True)
        self.setMinimumWidth(220)
        self.setMaximumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_conversation(self, convo: Conversation | None) -> None:
        self.convo = convo
        self.refresh()

    def refresh(self) -> None:
        self._compute()
        self.update()

    def _compute(self) -> None:
        self._pos = {}
        if self.convo is None or not self.convo.nodes:
            return
        root = self.convo.root_id()
        if root is None:
            return
        cursor = [0]

        def walk(nid: str, depth: int) -> float:
            kids = self.convo.children(nid)
            if not kids:
                x = float(cursor[0])
                cursor[0] += 1
            else:
                xs = [walk(k.id, depth + 1) for k in kids]
                x = (min(xs) + max(xs)) / 2.0
            self._pos[nid] = (x, float(depth))
            return x

        walk(root, 0)

    def _tick(self) -> None:
        self._phase += 0.12
        if self.isVisible():
            self.update()

    def _geometry(self) -> tuple[float, float, float, float, float, float]:
        if not self._pos:
            return 0, 0, 0, 0, 1, 1
        xs = [p[0] for p in self._pos.values()]
        ys = [p[1] for p in self._pos.values()]
        return min(xs), max(xs), min(ys), max(ys), 1.0, 1.0

    def _layout_pixels(self) -> None:
        self._pix = {}
        if not self._pos:
            return
        minx, maxx, miny, maxy, _, _ = self._geometry()
        top = 94.0
        bottom = self.height() - 92
        avail_h = max(60.0, bottom - top)
        avail_w = max(80.0, self.width() - 46.0)
        dx = max(1.0, maxx - minx)
        dy = max(1.0, maxy - miny)
        sx = min(avail_w / (dx + 0.35), 76.0)
        sy = min(avail_h / (dy + 0.55), 64.0)
        if sx < 5:
            sx = max(2.0, avail_w / dx)
        if sy < 5:
            sy = max(2.0, avail_h / dy)
        tree_w = dx * sx
        tree_h = dy * sy
        mid_x = (minx + maxx) / 2.0
        center_x = self.width() / 2.0
        offset_y = top + max(0.0, (avail_h - tree_h) / 2.0)
        for nid, (x, y) in self._pos.items():
            px = center_x + (x - mid_x) * sx
            py = offset_y + (y - miny) * sy
            self._pix[nid] = QPointF(px, py)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_pixels()

    def _active_ids(self) -> set[str]:
        if self.convo is None:
            return set()
        return {n.id for n in self.convo.view()}

    def _node_at(self, pos: QPointF) -> str | None:
        best = None
        best_d = 9.5
        for nid, p in self._pix.items():
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < best_d:
                best = nid
                best_d = d
        return best

    def mouseMoveEvent(self, event) -> None:
        self._layout_pixels()
        nid = self._node_at(event.position())
        if nid != self._hover:
            self._hover = nid
            self.setCursor(Qt.CursorShape.PointingHandCursor if nid else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            nid = self._node_at(event.position())
            if nid:
                self._hover = nid
                self.node_clicked.emit(nid)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), color(C.bg_soft))
        p.setPen(QPen(color(C.line_soft), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        self._layout_pixels()

        p.setFont(font(FONT_MONO, 8.2, 600, 1.5))
        p.setPen(color(C.brass_dim))
        p.drawText(QRectF(22, 22, self.width() - 40, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "ATLAS")
        p.setFont(font(FONT_UI, 10.5))
        p.setPen(color(C.ink_dim))
        stats = self.convo.stats() if self.convo else {"nodes": 0, "forks": 0}
        p.drawText(
            QRectF(22, 40, self.width() - 40, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{stats.get('nodes', 0)} 节点 · {stats.get('forks', 0)} 分叉",
        )

        if not self._pix:
            draw_logo(p, QRectF(self.width() / 2 - 25, self.height() / 2 - 120, 50, 50), color(C.line_hi), False)
            p.setFont(font(FONT_UI, 9.5))
            p.setPen(color(C.faint))
            p.drawText(
                QRectF(20, self.height() / 2 - 58, self.width() - 40, 24),
                Qt.AlignmentFlag.AlignHCenter,
                "还没有形状",
            )
            p.end()
            return

        active = self._active_ids()
        chain = self.convo.chain() if self.convo else []
        active_edges = set()
        for a, b in zip(chain, chain[1:]):
            active_edges.add((a.id, b.id))
        current = self.convo.view_leaf() if self.convo else None
        forks = {n.id for n in self.convo.nodes.values() if len(self.convo.children(n.id)) > 1} if self.convo else set()

        # edges
        for nid, pt in self._pix.items():
            node = self.convo.nodes.get(nid) if self.convo else None
            if node is None:
                continue
            x, y = pt.x(), pt.y()
            for child in self.convo.children(nid):
                to = self._pix.get(child.id)
                if to is None:
                    continue
                path = QPainterPath()
                path.moveTo(x, y + 2.4)
                path.cubicTo(x, y + 9, to.x(), to.y() - 9, to.x(), to.y() - 2.2)
                if (nid, child.id) in active_edges:
                    p.setPen(QPen(color(C.brass, 220), 1.8))
                else:
                    p.setPen(QPen(color(C.line), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

        # nodes
        for nid, pt in self._pix.items():
            is_active = nid in active
            is_current = nid == current
            if is_current:
                pulse = 9.0 + 1.8 * (0.5 + 0.5 * math.sin(self._phase))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(C.brass, 24))
                p.drawEllipse(QRectF(pt.x() - pulse, pt.y() - pulse, pulse * 2, pulse * 2))
                p.setPen(QPen(color(C.brass, 90), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(pt.x() - 6, pt.y() - 6, 12, 12))
            if nid in forks:
                r = 3.8 if not is_active else 4.5
                path = QPainterPath()
                path.moveTo(pt.x(), pt.y() - r)
                path.lineTo(pt.x() + r, pt.y())
                path.lineTo(pt.x(), pt.y() + r)
                path.lineTo(pt.x() - r, pt.y())
                path.closeSubpath()
                p.setPen(QPen(color(C.bg_soft), 1))
                p.setBrush(color(C.brass if is_active else C.brass_deep if nid == self._hover else C.muted))
                p.drawPath(path)
            else:
                r = 2.7 if not is_active else 3.6
                if is_current:
                    r = 4.6
                p.setPen(QPen(color(C.bg_soft), 1))
                p.setBrush(color(C.brass if is_active else C.ink_dim if nid == self._hover else C.faint))
                p.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))

        # hover preview
        if self._hover and self._hover in (self.convo.nodes if self.convo else {}):
            node = self.convo.nodes[self._hover]
            box = QRectF(16, self.height() - 76, self.width() - 32, 46)
            p.setPen(QPen(color(C.line), 1))
            p.setBrush(color(C.panel_hi))
            p.drawRoundedRect(box, 7, 7)
            role = "你" if node.role == "user" else "模型"
            p.setFont(font(FONT_MONO, 7.6, 500, 0.6))
            p.setPen(color(C.brass if node.role == "user" else C.ink_dim))
            p.drawText(box.adjusted(9, 5, -9, -30), Qt.AlignmentFlag.AlignLeft, role)
            p.setFont(font(FONT_UI, 8.4))
            p.setPen(color(C.ink_dim))
            fm = QFontMetrics(font(FONT_UI, 8.4))
            preview = one_line(node.text or "（空）", 58)
            p.drawText(
                box.adjusted(9, 18, -9, -5),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(preview, Qt.TextElideMode.ElideRight, int(box.width()) - 18),
            )

        # legend
        p.setFont(font(FONT_MONO, 7.4, 400, 0.4))
        ly = self.height() - 24
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.brass))
        p.drawEllipse(QRectF(22, ly - 2.5, 5, 5))
        p.setPen(color(C.faint))
        p.drawText(QRectF(32, ly - 9, 70, 18), Qt.AlignmentFlag.AlignVCenter, "当前路径")
        p.setPen(QPen(color(C.brass_deep), 1))
        p.setBrush(color(C.brass_deep))
        diamond = QPainterPath()
        diamond.moveTo(124, ly - 3.2)
        diamond.lineTo(128, ly)
        diamond.lineTo(124, ly + 3.2)
        diamond.lineTo(120, ly)
        diamond.closeSubpath()
        p.drawPath(diamond)
        p.setPen(color(C.faint))
        p.drawText(QRectF(134, ly - 9, 90, 18), Qt.AlignmentFlag.AlignVCenter, "分叉 / 点击跳转")
        p.end()