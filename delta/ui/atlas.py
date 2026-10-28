"""Atlas: the whole tree, fitted into a quiet panel.

The transcript shows one path; this is the only place where the shape of the
whole conversation is visible at once.  Nodes are dots, forks are diamonds.

What the drawing says
---------------------
* solid edge   the default child of its parent (the walk would go there)
* dashed edge  a sibling that was not made the default
* thin brass, root -> baseline: the shortcut chain.  Every step is somebody's
  parent, so it is structural fact, not a choice.
* thick brass with a soft underglow, baseline -> leaf: the route.  Every step is
  a default you picked, and this is where you are reading.
* a bar under the baseline dot, the way a letter sits on its baseline.

Clicking a node sets the baseline there; the lock set follows.  Nothing else
in here writes.
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

SHORTCUT, ROUTE, PLAIN = "shortcut", "route", "plain"


def edge_sets(convo: Conversation | None) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(shortcut edges, route edges), each a set of (parent_id, child_id).

    The shortcut is root -> baseline, a chain of parent links.  The route starts
    at the baseline and follows defaults down to the leaf, so its first edge is
    (baseline, first default child).
    """
    if convo is None or not convo.nodes:
        return set(), set()
    shortcut = [n.id for n in convo.shortcut_chain()]
    route = [n.id for n in convo.route()]
    s_edges = set(zip(shortcut, shortcut[1:]))
    r_edges = set(zip([convo.baseline_id] + route, route)) if route else set()
    return s_edges, r_edges


def edge_role(convo: Conversation | None, parent_id: str, child_id: str) -> str:
    """Which part of the view an edge belongs to: ROUTE, SHORTCUT or PLAIN."""
    s_edges, r_edges = edge_sets(convo)
    pair = (parent_id, child_id)
    if pair in r_edges:
        return ROUTE
    if pair in s_edges:
        return SHORTCUT
    return PLAIN


def edge_is_default(convo: Conversation | None, parent_id: str, child_id: str) -> bool:
    """True when child is the default child of parent (one per layer)."""
    parent = convo.nodes.get(parent_id) if convo is not None else None
    return parent is not None and parent.default_child == child_id


def node_tags(convo: Conversation | None, node_id: str) -> list[str]:
    """Short words for what a node is, in priority order (hover box, tests)."""
    if convo is None or node_id not in convo.nodes:
        return []
    tags: list[str] = []
    if convo.baseline_id == node_id:
        tags.append("基准")
    if convo.view_leaf() == node_id:
        tags.append("当前")
    on_shortcut = node_id in {n.id for n in convo.shortcut_chain()}
    on_route = node_id in {n.id for n in convo.route()}
    if on_shortcut and convo.baseline_id != node_id:
        tags.append("捷径链")
    if on_route:
        tags.append("路线")
    if len(convo.children(node_id)) > 1:
        tags.append("分叉")
    if not tags:
        tags.append("分支")
    return tags


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        sx = min(avail_w / (dx + 0.35), 120.0)
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

    # -- painting ------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), color(C.bg_soft))
        p.setPen(QPen(color(C.line_soft), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        self._layout_pixels()

        self._draw_header(p)
        if not self._pix:
            self._draw_empty(p)
            p.end()
            return

        self._draw_horizon(p)
        s_edges, r_edges = edge_sets(self.convo)
        self._draw_edges(p, s_edges, r_edges)
        self._draw_nodes(p)
        self._draw_hover(p)
        self._draw_legend(p)
        p.end()

    def _draw_horizon(self, p: QPainter) -> None:
        """Two short ticks at the baseline's level: fact above it, choice below."""
        if self.convo is None or self.convo.baseline_id not in self._pix:
            return
        pt = self._pix[self.convo.baseline_id]
        p.setPen(QPen(color(C.brass, 90), 1.0))
        p.drawLine(QPointF(pt.x() - 27, pt.y()), QPointF(pt.x() - 12, pt.y()))
        p.drawLine(QPointF(pt.x() + 12, pt.y()), QPointF(pt.x() + 27, pt.y()))

    def _draw_header(self, p: QPainter) -> None:
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

    def _draw_empty(self, p: QPainter) -> None:
        draw_logo(p, QRectF(self.width() / 2 - 25, self.height() / 2 - 120, 50, 50), color(C.line_hi), False)
        p.setFont(font(FONT_UI, 9.5))
        p.setPen(color(C.faint))
        p.drawText(
            QRectF(20, self.height() / 2 - 58, self.width() - 40, 24),
            Qt.AlignmentFlag.AlignHCenter,
            "还没有形状",
        )

    def _edge_pen(self, pair: tuple[str, str], s_edges, r_edges) -> QPen:
        parent_id, child_id = pair
        default = edge_is_default(self.convo, parent_id, child_id)
        if pair in r_edges:
            pen = QPen(color(C.brass, 245), 2.5)
        elif pair in s_edges:
            pen = QPen(color(C.ink_dim, 230), 1.7)
        elif default:
            pen = QPen(color(C.line_hi, 255), 1.6)
        else:
            pen = QPen(color(C.line, 255), 1.1)
        if not default:
            pen.setDashPattern([2.4, 4.0])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    def _edge_path(self, a: QPointF, b: QPointF) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(a.x(), a.y() + 2.4)
        path.cubicTo(a.x(), a.y() + 9, b.x(), b.y() - 9, b.x(), b.y() - 2.2)
        return path

    def _draw_edges(self, p: QPainter, s_edges, r_edges) -> None:
        convo = self.convo
        edges: list[tuple[tuple[str, str], QPainterPath]] = []
        for nid, pt in self._pix.items():
            node = convo.nodes.get(nid) if convo else None
            if node is None:
                continue
            for child in convo.children(nid):
                to = self._pix.get(child.id)
                if to is None:
                    continue
                edges.append(((nid, child.id), self._edge_path(pt, to)))

        # the route carries a soft halo; draw every halo first so lines stay crisp
        glow = QPen(color(C.brass, 18), 10.0)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        core = QPen(color(C.brass, 30), 5.2)
        core.setCapStyle(Qt.PenCapStyle.RoundCap)
        for pen in (glow, core):
            for pair, path in edges:
                if pair in r_edges:
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)

        for pair, path in edges:
            p.setPen(self._edge_pen(pair, s_edges, r_edges))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

    def _draw_nodes(self, p: QPainter) -> None:
        convo = self.convo
        if convo is None:
            return
        base = convo.baseline_id
        leaf = convo.view_leaf()
        on_route = {n.id for n in convo.route()}
        on_shortcut = {n.id for n in convo.shortcut_chain()}
        forks = {n.id for n in convo.nodes.values() if len(convo.children(n.id)) > 1}

        def node_ink(nid: str) -> str:
            if nid == base:
                return C.brass_hi
            if nid in on_route:
                return C.brass
            if nid in on_shortcut:
                return C.ink_soft if nid == self._hover else C.ink_dim
            return C.ink_soft if nid == self._hover else C.faint

        for nid, pt in self._pix.items():
            is_current = nid == leaf
            is_base = nid == base
            is_fork = nid in forks

            if is_current:
                pulse = 9.0 + 1.8 * (0.5 + 0.5 * math.sin(self._phase))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(C.brass, 24))
                p.drawEllipse(QRectF(pt.x() - pulse, pt.y() - pulse, pulse * 2, pulse * 2))
                if not is_base:
                    p.setPen(QPen(color(C.brass, 90), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QRectF(pt.x() - 6, pt.y() - 6, 12, 12))

            if is_base:
                p.setPen(QPen(color(C.brass, 235), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(pt.x() - 6.4, pt.y() - 6.4, 12.8, 12.8))
                p.setPen(QPen(color(C.brass, 110), 1.0))
                p.drawEllipse(QRectF(pt.x() - 9.4, pt.y() - 9.4, 18.8, 18.8))

            if is_fork:
                r = 4.5 if nid in on_route else 3.8
                path = QPainterPath()
                path.moveTo(pt.x(), pt.y() - r)
                path.lineTo(pt.x() + r, pt.y())
                path.lineTo(pt.x(), pt.y() + r)
                path.lineTo(pt.x() - r, pt.y())
                path.closeSubpath()
                p.setPen(QPen(color(C.bg_soft), 1))
                p.setBrush(color(node_ink(nid)))
                p.drawPath(path)
            else:
                r = 3.6 if nid in on_route else 2.7
                if is_current:
                    r = 4.6
                if is_base:
                    r = max(r, 4.2)
                p.setPen(QPen(color(C.bg_soft), 1))
                p.setBrush(color(node_ink(nid)))
                p.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))


    def _draw_hover(self, p: QPainter) -> None:
        nid = self._hover
        if not nid or self.convo is None or nid not in self.convo.nodes:
            return
        node = self.convo.nodes[nid]
        box = QRectF(14, self.height() - 132, self.width() - 28, 62)
        p.setPen(QPen(color(C.line), 1))
        p.setBrush(color(C.panel_hi))
        p.drawRoundedRect(box, 7, 7)

        role = "你" if node.role == "user" else "模型"
        tags = node_tags(self.convo, nid)
        head = role + (" · " + " · ".join(tags) if tags else "")
        p.setFont(font(FONT_MONO, 7.6, 500, 0.6))
        p.setPen(color(C.brass if node.role == "user" else C.ink_dim))
        p.drawText(
            QRectF(box.left() + 9, box.top() + 5, box.width() - 18, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            head,
        )

        p.setFont(font(FONT_UI, 8.4))
        p.setPen(color(C.ink_dim))
        fm = QFontMetrics(font(FONT_UI, 8.4))
        preview = one_line(node.text or "（空）", 58)
        p.drawText(
            QRectF(box.left() + 9, box.top() + 21, box.width() - 18, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(preview, Qt.TextElideMode.ElideRight, int(box.width()) - 18),
        )

        p.setFont(font(FONT_MONO, 6.9, 400, 0.3))
        p.setPen(color(C.muted))
        p.drawText(
            QRectF(box.left() + 9, box.top() + 42, box.width() - 18, 12),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "点击 → 把基准设到这里",
        )

    def _draw_legend(self, p: QPainter) -> None:
        f = font(FONT_MONO, 6.8, 400, 0.25)
        p.setFont(f)
        fm = QFontMetrics(f)
        ink = color(C.muted)
        rows = (self.height() - 56, self.height() - 36, self.height() - 16)
        col2 = 128.0

        def label(x: float, y: float, text: str) -> float:
            w = fm.horizontalAdvance(text)
            p.setPen(ink)
            p.drawText(
                QRectF(x, y - 7, w + 2, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            return x + w

        def rule(x: float, y: float, pen: QPen, *, glow: bool = False) -> None:
            if glow:
                p.setPen(QPen(color(C.brass, 30), 5.4))
                p.drawLine(QPointF(x, y), QPointF(x + 14, y))
            p.setPen(pen)
            p.drawLine(QPointF(x, y), QPointF(x + 14, y))

        def dot(x: float, y: float) -> None:
            p.setPen(QPen(color(C.brass, 90), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - 5.2, y - 5.2, 10.4, 10.4))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass))
            p.drawEllipse(QRectF(x - 3.0, y - 3.0, 6.0, 6.0))

        route_pen = QPen(color(C.brass, 245), 2.5)
        route_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        dashed = QPen(color(C.line, 255), 1.1)
        dashed.setDashPattern([2.4, 4.0])

        # column 1: the three line styles
        rule(21, rows[0], QPen(color(C.ink_dim, 230), 1.7))
        label(40, rows[0], "捷径 · 结构")
        rule(21, rows[1], route_pen, glow=True)
        label(40, rows[1], "路线 · 默认")
        rule(21, rows[2], dashed)
        label(40, rows[2], "未选")

        # column 2: the three node marks
        y = rows[0]
        p.setPen(QPen(color(C.brass, 90), 1.0))
        p.drawLine(QPointF(col2 - 9.2, y), QPointF(col2 - 7.0, y))
        p.drawLine(QPointF(col2 + 7.0, y), QPointF(col2 + 9.2, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.brass_hi))
        p.drawEllipse(QRectF(col2 - 2.0, y - 2.0, 4.0, 4.0))
        p.setPen(QPen(color(C.brass, 235), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(col2 - 4.2, y - 4.2, 8.4, 8.4))
        p.setPen(QPen(color(C.brass, 110), 1.0))
        p.drawEllipse(QRectF(col2 - 6.2, y - 6.2, 12.4, 12.4))
        label(col2 + 14, y, "基准")

        y = rows[1]
        dot(col2, y)
        label(col2 + 12, y, "当前")

        y = rows[2]
        d = QPainterPath()
        d.moveTo(col2, y - 4.2)
        d.lineTo(col2 + 4.2, y)
        d.lineTo(col2, y + 4.2)
        d.lineTo(col2 - 4.2, y)
        d.closeSubpath()
        p.setPen(QPen(color(C.bg_soft), 1))
        p.setBrush(color(C.muted))
        p.drawPath(d)
        label(col2 + 12, y, "分叉 · 点击")
