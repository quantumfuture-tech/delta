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
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.model import Conversation, one_line
from .icons import draw_logo
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import fmt_count

SHORTCUT, ROUTE, PLAIN = "shortcut", "route", "plain"

# -- geometry ---------------------------------------------------------------
RULER_L = 18.0          # left ruler: one tick per layer
RULER_T = 18.0          # top ruler: one tick per leaf slot
Y_MIN, Y_MAX = 22.0, 180.0
X_MIN, X_MAX = 8.0, 260.0
ZOOM_STEP = 1.1
DRAG_PX = 6.0           # past this a press becomes a drag, not a click
MM_MIN = 56.0
MM_CHIP = 24.0
MM_W, MM_H = 76.0, 104.0
SNAP_PX = 12.0


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

        # two axes, each either fitted to the panel or pinned to a value in px
        # per unit (one unit = one layer vertically, one leaf slot horizontally)
        self._axis: dict[str, dict] = {
            "y": {"mode": "fit", "value": 48.0},
            "x": {"mode": "fit", "value": 26.0},
        }
        self._unit = {"x": 26.0, "y": 48.0}
        self._scroll = QPointF(0.0, 0.0)
        self._overflow = QPointF(0.0, 0.0)
        self._settings: dict | None = None
        self._minimap: dict | None = None
        self._mini_open = True
        self._drag: str | None = None
        self._drag_start = None
        self._follow = True
        self._press: dict | None = None

    def set_conversation(self, convo: Conversation | None) -> None:
        self.convo = convo
        self.refresh()

    def refresh(self) -> None:
        self._compute()
        self._follow_current()
        self._layout_pixels()
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

    # -- geometry ------------------------------------------------------
    def plot_rect(self) -> QRectF:
        """The canvas: below the header and top ruler, above the legend."""
        return QRectF(
            RULER_L + 4.0,
            96.0,
            max(40.0, self.width() - RULER_L - 4.0 - 12.0),
            max(40.0, self.height() - 96.0 - 92.0),
        )

    def _fit_value(self, axis: str, avail: float) -> float:
        if not self._pos:
            return 48.0 if axis == "y" else 26.0
        minx, maxx, miny, maxy, _, _ = self._geometry()
        if axis == "y":
            return min(avail / (max(1.0, maxy - miny) + 0.55), 64.0)
        return min(avail / (max(1.0, maxx - minx) + 0.35), 120.0)

    def axis_value(self, axis: str) -> float:
        state = self._axis[axis]
        if state["mode"] == "fixed":
            lo, hi = (Y_MIN, Y_MAX) if axis == "y" else (X_MIN, X_MAX)
            return max(lo, min(hi, state["value"]))
        return self._fit_value(axis, self.plot_rect().height() if axis == "y" else self.plot_rect().width())

    def _layout_pixels(self) -> None:
        self._pix = {}
        if not self._pos:
            return
        minx, maxx, miny, maxy, _, _ = self._geometry()
        plot = self.plot_rect()
        dx = max(1.0, maxx - minx)
        dy = max(1.0, maxy - miny)

        sy = self.axis_value("y")
        sx = self.axis_value("x")
        self._unit = {"x": sx, "y": sy}

        width_px = dx * sx
        height_px = dy * sy
        self._overflow = QPointF(max(0.0, width_px - plot.width()), max(0.0, height_px - plot.height()))
        self._scroll = QPointF(
            min(max(0.0, self._scroll.x()), self._overflow.x()),
            min(max(0.0, self._scroll.y()), self._overflow.y()),
        )

        if self._axis["x"]["mode"] == "fit" or width_px <= plot.width():
            origin_x = plot.center().x() - (minx + maxx) / 2.0 * sx
        else:
            origin_x = plot.left() - minx * sx - self._scroll.x()
        if self._axis["y"]["mode"] == "fit" or height_px <= plot.height():
            origin_y = plot.top() + (plot.height() - height_px) / 2.0 - miny * sy
        else:
            origin_y = plot.top() - miny * sy - self._scroll.y()

        for nid, (x, y) in self._pos.items():
            self._pix[nid] = QPointF(origin_x + x * sx, origin_y + y * sy)

    def overflow(self) -> QPointF:
        return self._overflow

    # -- axes ----------------------------------------------------------
    def bind_settings(self, settings: dict) -> None:
        self._settings = settings
        axis = settings.get("atlas_axis")
        if isinstance(axis, dict):
            for key in ("x", "y"):
                state = axis.get(key)
                if isinstance(state, dict) and state.get("mode") == "fixed":
                    self._axis[key] = {"mode": "fixed", "value": float(state.get("value", self._axis[key]["value"]))}
        mini = settings.get("atlas_minimap")
        if isinstance(mini, dict):
            self._minimap = {k: float(mini.get(k, 0.0)) for k in ("x", "y", "w", "h")}
        self._mini_open = bool(settings.get("atlas_minimap_open", True))

    def _persist(self) -> None:
        if self._settings is None:
            return
        self._settings["atlas_axis"] = {
            key: {"mode": state["mode"], "value": round(state["value"], 1)}
            for key, state in self._axis.items()
        }
        if self._minimap is not None:
            self._settings["atlas_minimap"] = {k: round(v, 1) for k, v in self._minimap.items()}
        self._settings["atlas_minimap_open"] = self._mini_open

    def set_minimap_open(self, open_: bool) -> None:
        if self._mini_open == open_:
            return
        self._mini_open = open_
        self._persist()
        self.update()

    def _zoom(self, factor: float) -> None:
        """Scale every pinned axis; fitted axes have nothing to scale."""
        changed = False
        for axis, (lo, hi) in (("x", (X_MIN, X_MAX)), ("y", (Y_MIN, Y_MAX))):
            state = self._axis[axis]
            if state["mode"] != "fixed":
                continue
            state["value"] = max(lo, min(hi, state["value"] * factor))
            changed = True
        if changed:
            self._layout_pixels()
            self._persist()
            self.update()

    def _follow_current(self) -> None:
        """Keep the node being read inside the canvas after a jump."""
        if not self._follow or not self._pos or self.convo is None:
            return
        target = self.convo.view_leaf() or self.convo.baseline_id
        if target is None or target not in self._pos:
            return
        plot = self.plot_rect()
        self._layout_pixels()
        px = self._pix[target]
        dx = 0.0
        dy = 0.0
        if px.y() < plot.top() + 12:
            dy = px.y() - (plot.top() + plot.height() / 2)
        elif px.y() > plot.bottom() - 12:
            dy = px.y() - (plot.top() + plot.height() / 2)
        if px.x() < plot.left() + 12:
            dx = px.x() - (plot.left() + plot.width() / 2)
        elif px.x() > plot.right() - 12:
            dx = px.x() - (plot.left() + plot.width() / 2)
        if dx == 0.0 and dy == 0.0:
            return
        self._scroll = QPointF(
            min(max(0.0, self._scroll.x() + dx), self._overflow.x()),
            min(max(0.0, self._scroll.y() + dy), self._overflow.y()),
        )
        self._layout_pixels()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_pixels()
        self.update()

    # -- minimap -------------------------------------------------------
    def minimap_rect(self) -> QRectF:
        """The overview box in panel coordinates, always inside the canvas."""
        plot = self.plot_rect()
        if self._minimap is None:
            w = min(MM_W, max(MM_MIN, plot.width() - 24.0))
            h = min(MM_H, max(MM_MIN, plot.height() - 24.0))
            self._minimap = {
                "x": plot.right() - 12.0 - w,
                "y": plot.top() + 12.0,
                "w": w,
                "h": h,
            }
        box = self._minimap
        box["w"] = max(MM_MIN, min(float(box["w"]), plot.width() - 8.0))
        box["h"] = max(MM_MIN, min(float(box["h"]), plot.height() - 8.0))
        box["x"] = max(plot.left(), min(float(box["x"]), plot.right() - box["w"]))
        box["y"] = max(plot.top(), min(float(box["y"]), plot.bottom() - box["h"]))
        if not self._mini_open:
            # collapsed: a small square hanging off the same top-right anchor
            return QRectF(box["x"] + box["w"] - MM_CHIP, box["y"], MM_CHIP, MM_CHIP)
        return QRectF(box["x"], box["y"], box["w"], box["h"])

    def _mini_fit(self, box: QRectF) -> tuple[float, float, float]:
        """Uniform content -> minimap scale; both axes fixed by the content box."""
        minx, maxx, miny, maxy, _, _ = self._geometry()
        dx = max(1.0, maxx - minx)
        dy = max(1.0, maxy - miny)
        pad = 7.0
        k = min((box.width() - 2 * pad) / dx, (box.height() - 2 * pad) / dy)
        ox = box.left() + (box.width() - dx * k) / 2.0 - minx * k
        oy = box.top() + (box.height() - dy * k) / 2.0 - miny * k
        return k, ox, oy

    def _viewport_frame(self, box: QRectF) -> QRectF:
        k, ox, oy = self._mini_fit(box)
        plot = self.plot_rect()
        ux = max(1e-6, self._unit["x"])
        uy = max(1e-6, self._unit["y"])
        fw = max(6.0, min(box.width() - 4.0, plot.width() / ux * k))
        fh = max(6.0, min(box.height() - 4.0, plot.height() / uy * k))
        return QRectF(ox + self._scroll.x() / ux * k, oy + self._scroll.y() / uy * k, fw, fh)

    def minimap_zone(self, pos: QPointF) -> str | None:
        if self._overflow.x() <= 0.5 and self._overflow.y() <= 0.5:
            return None
        box = self.minimap_rect()
        if not self._mini_open:
            return "chip" if box.contains(pos) else None
        if not box.contains(pos):
            return None
        if pos.x() < box.left() + 13.0 and pos.y() < box.top() + 13.0:
            return "collapse"
        if pos.x() > box.right() - 15.0 and pos.y() < box.top() + 15.0:
            return "move"
        if pos.x() < box.left() + 13.0 and pos.y() > box.bottom() - 13.0:
            return "resize"
        return "pan" if self._viewport_frame(box).contains(pos) else "jump"

    def _minimap_center(self, pos: QPointF) -> None:
        box = self.minimap_rect()
        k, ox, oy = self._mini_fit(box)
        plot = self.plot_rect()
        self._scroll = QPointF(
            min(max(0.0, (pos.x() - ox) / k * self._unit["x"] - plot.width() / 2.0), self._overflow.x()),
            min(max(0.0, (pos.y() - oy) / k * self._unit["y"] - plot.height() / 2.0), self._overflow.y()),
        )

    def _snap_minimap(self) -> None:
        plot = self.plot_rect()
        box = self.minimap_rect()
        if abs(box.top() - (plot.top() + 12.0)) < SNAP_PX and abs(box.right() - (plot.right() - 12.0)) < SNAP_PX:
            self._minimap["x"] = plot.right() - 12.0 - box.width()
            self._minimap["y"] = plot.top() + 12.0

    def _ruler_zone(self, pos: QPointF) -> str | None:
        plot = self.plot_rect()
        if pos.x() <= RULER_L and plot.top() <= pos.y() <= plot.bottom():
            return "y"
        if 76.0 <= pos.y() <= 94.0 and pos.x() >= plot.left():
            return "x"
        return None

    # -- pointer -------------------------------------------------------
    def _node_at(self, pos: QPointF) -> str | None:
        if not self.plot_rect().contains(pos):
            return None
        best = None
        best_d = 9.5
        for nid, p in self._pix.items():
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < best_d:
                best = nid
                best_d = d
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()
        zone = self.minimap_zone(pos)
        if zone == "chip":
            self.set_minimap_open(True)
            return
        if zone == "collapse":
            self.set_minimap_open(False)
            return
        if zone is not None:
            self._drag = "mm_" + zone
            self._drag_start = {"pos": pos, "box": dict(self._minimap or {})}
            if zone in ("pan", "jump"):
                self._follow = False
                self._minimap_center(pos)
                self.update()
            return
        ruler = self._ruler_zone(pos)
        if ruler is not None:
            state = self._axis[ruler]
            if state["mode"] == "fit":
                state["mode"] = "fixed"
                state["value"] = self._unit[ruler]
            self._drag = "ruler_" + ruler
            self._drag_start = {"pos": pos, "value": state["value"]}
            return
        self._press = {"pos": pos, "scroll": QPointF(self._scroll), "moved": False}

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag == "mm_move":
            delta = pos - self._drag_start["pos"]
            start = self._drag_start["box"]
            self._minimap["x"] = start.get("x", 0.0) + delta.x()
            self._minimap["y"] = start.get("y", 0.0) + delta.y()
            self.minimap_rect()
            self.update()
            return
        if self._drag == "mm_resize":
            start = self._drag_start["box"]
            right = start.get("x", 0.0) + start.get("w", MM_W)
            width = max(MM_MIN, right - pos.x())
            self._minimap["x"] = right - width
            self._minimap["w"] = width
            self._minimap["h"] = max(MM_MIN, pos.y() - start.get("y", 0.0))
            self.minimap_rect()
            self.update()
            return
        if self._drag in ("mm_pan", "mm_jump"):
            self._minimap_center(pos)
            self.update()
            return
        if self._drag is not None and self._drag.startswith("ruler_"):
            axis = self._drag.split("_")[1]
            start = self._drag_start
            delta = (pos.y() - start["pos"].y()) if axis == "y" else (pos.x() - start["pos"].x())
            lo, hi = (Y_MIN, Y_MAX) if axis == "y" else (X_MIN, X_MAX)
            self._axis[axis]["value"] = max(lo, min(hi, start["value"] * (1.0 + delta / 240.0)))
            self._layout_pixels()
            self.update()
            return
        if self._press is not None:
            if not self._press["moved"] and (pos - self._press["pos"]).manhattanLength() > DRAG_PX:
                self._press["moved"] = True
                self._drag = "pan"
                self._follow = False
            if self._drag == "pan":
                delta = pos - self._press["pos"]
                self._scroll = QPointF(
                    min(max(0.0, self._press["scroll"].x() - delta.x()), self._overflow.x()),
                    min(max(0.0, self._press["scroll"].y() - delta.y()), self._overflow.y()),
                )
                self._layout_pixels()
                self.update()
                return

        zone = self.minimap_zone(pos)
        ruler = self._ruler_zone(pos)
        if zone == "move":
            shape = Qt.CursorShape.SizeAllCursor
        elif zone == "resize":
            shape = Qt.CursorShape.SizeFDiagCursor
        elif zone in ("chip", "collapse"):
            shape = Qt.CursorShape.PointingHandCursor
        elif zone is not None:
            shape = Qt.CursorShape.OpenHandCursor
        elif ruler == "y":
            shape = Qt.CursorShape.SizeVerCursor
        elif ruler == "x":
            shape = Qt.CursorShape.SizeHorCursor
        else:
            shape = None
        nid = self._node_at(pos)
        if shape is None:
            shape = Qt.CursorShape.PointingHandCursor if nid else Qt.CursorShape.ArrowCursor
        if self.cursor().shape() != shape:
            self.setCursor(shape)
        if nid != self._hover:
            self._hover = nid
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        drag, self._drag = self._drag, None
        press, self._press = self._press, None
        if drag is not None:
            if drag == "mm_move":
                self._snap_minimap()
            if drag.startswith("mm_") or drag.startswith("ruler_"):
                self._persist()
                self.update()
            return
        if press is None:
            return
        nid = self._node_at(pos)
        if nid:
            self._hover = nid
            self._follow = True
            self.node_clicked.emit(nid)

    def mouseDoubleClickEvent(self, event) -> None:
        ruler = self._ruler_zone(event.position())
        if ruler is not None:
            self._axis[ruler]["mode"] = "fit"
            self._layout_pixels()
            self._persist()
            self.update()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = event.angleDelta().y()
            if step:
                self._zoom(ZOOM_STEP if step > 0 else 1.0 / ZOOM_STEP)
                event.accept()
                return
        dy = self._overflow.y() > 0.5
        dx = self._overflow.x() > 0.5
        if not dy and not dx:
            event.ignore()
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        self._follow = False
        if dy:
            self._scroll.setY(min(max(0.0, self._scroll.y() - delta), self._overflow.y()))
        else:
            self._scroll.setX(min(max(0.0, self._scroll.x() - delta), self._overflow.x()))
        self._layout_pixels()
        self.update()
        event.accept()

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

        plot = self.plot_rect()
        p.save()
        p.setClipRect(plot)
        self._draw_horizon(p)
        s_edges, r_edges = edge_sets(self.convo)
        self._draw_edges(p, s_edges, r_edges)
        self._draw_nodes(p)
        p.restore()

        self._draw_rulers(p)
        self._draw_fades(p)
        if self._overflow.x() > 0.5 or self._overflow.y() > 0.5:
            self._draw_minimap(p)
        self._draw_hover(p)
        self._draw_legend(p)
        p.end()

    def _draw_rulers(self, p: QPainter) -> None:
        """Two scales: one tick per layer on the left, per leaf slot on top."""
        plot = self.plot_rect()
        fixed_y = self._axis["y"]["mode"] == "fixed"
        p.setPen(QPen(color(C.line_hi, 170), 1))
        p.drawLine(QPointF(RULER_L, plot.top()), QPointF(RULER_L, plot.bottom()))
        for index, y in enumerate(sorted({round(pt.y(), 1) for pt in self._pix.values()})):
            if y < plot.top() - 1 or y > plot.bottom() + 1:
                continue
            length = 9.0 if index % 5 == 0 else 5.0
            p.setPen(QPen(color(C.ink_dim if fixed_y else C.line_hi, 210), 1))
            p.drawLine(QPointF(RULER_L - length, y), QPointF(RULER_L, y))

        fixed_x = self._axis["x"]["mode"] == "fixed"
        p.setPen(QPen(color(C.line_hi, 170), 1))
        p.drawLine(QPointF(plot.left(), 94.0), QPointF(plot.right(), 94.0))
        for index, x in enumerate(sorted({round(pt.x(), 1) for pt in self._pix.values()})):
            if x < plot.left() - 1 or x > plot.right() + 1:
                continue
            length = 9.0 if index % 5 == 0 else 5.0
            p.setPen(QPen(color(C.ink_dim if fixed_x else C.line_hi, 210), 1))
            p.drawLine(QPointF(x, 94.0 - length), QPointF(x, 94.0))

        p.setFont(font(FONT_MONO, 7.0))
        p.setPen(color(C.brass_dim if fixed_y else C.faint))
        p.drawText(
            QRectF(1, plot.top() - 15, RULER_L + 30, 12),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self._unit['y']:.0f}" if fixed_y else "适应",
        )
        p.setPen(color(C.brass_dim if fixed_x else C.faint))
        p.drawText(
            QRectF(plot.right() - 74, 62, 72, 12),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"1={self._unit['x']:.0f}" if fixed_x else "适应",
        )

    def _draw_fades(self, p: QPainter) -> None:
        """A gradient at each overflowing end: there is more in that direction."""
        plot = self.plot_rect()
        fade = 16.0
        solid = color(C.bg_soft, 255)
        clear = color(C.bg_soft, 0)
        if self._overflow.y() > 0.5:
            if self._scroll.y() > 0.5:
                grad = QLinearGradient(0, plot.top(), 0, plot.top() + fade)
                grad.setColorAt(0.0, solid)
                grad.setColorAt(1.0, clear)
                p.fillRect(QRectF(plot.left(), plot.top(), plot.width(), fade), grad)
            if self._scroll.y() < self._overflow.y() - 0.5:
                grad = QLinearGradient(0, plot.bottom() - fade, 0, plot.bottom())
                grad.setColorAt(0.0, clear)
                grad.setColorAt(1.0, solid)
                p.fillRect(QRectF(plot.left(), plot.bottom() - fade, plot.width(), fade), grad)
        if self._overflow.x() > 0.5:
            if self._scroll.x() > 0.5:
                grad = QLinearGradient(plot.left(), 0, plot.left() + fade, 0)
                grad.setColorAt(0.0, solid)
                grad.setColorAt(1.0, clear)
                p.fillRect(QRectF(plot.left(), plot.top(), fade, plot.height()), grad)
            if self._scroll.x() < self._overflow.x() - 0.5:
                grad = QLinearGradient(plot.right() - fade, 0, plot.right(), 0)
                grad.setColorAt(0.0, clear)
                grad.setColorAt(1.0, solid)
                p.fillRect(QRectF(plot.right() - fade, plot.top(), fade, plot.height()), grad)

    def _draw_minimap(self, p: QPainter) -> None:
        box = self.minimap_rect()
        if not self._mini_open:
            p.setPen(QPen(color(C.line_hi, 235), 1))
            p.setBrush(color(C.bg, 236))
            p.drawRect(box)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass, 220))
            for fx, fy in ((0.3, 0.32), (0.5, 0.56), (0.7, 0.78)):
                p.drawEllipse(QPointF(box.left() + box.width() * fx, box.top() + box.height() * fy), 1.5, 1.5)
            p.setPen(QPen(color(C.ink_dim, 210), 1.2))
            p.drawLine(QPointF(box.left() + 5, box.top() + 5), QPointF(box.left() + 10, box.top() + 5))
            return
        k, ox, oy = self._mini_fit(box)
        p.setPen(QPen(color(C.line_hi, 235), 1))
        p.setBrush(color(C.bg, 236))
        p.drawRect(box)

        # collapse handle: a short bar in the top-left corner, the only free one
        p.setPen(QPen(color(C.ink_dim, 200), 1.2))
        p.drawLine(QPointF(box.left() + 4, box.top() + 7), QPointF(box.left() + 10, box.top() + 7))

        convo = self.convo
        if convo is not None:
            p.setPen(QPen(color(C.faint, 120), 0.8))
            for nid, (x, y) in self._pos.items():
                for child in convo.children(nid):
                    to = self._pos.get(child.id)
                    if to is None:
                        continue
                    p.drawLine(QPointF(ox + x * k, oy + y * k), QPointF(ox + to[0] * k, oy + to[1] * k))

            route = {n.id for n in convo.route()}
            p.setPen(Qt.PenStyle.NoPen)
            for nid, (x, y) in self._pos.items():
                p.setBrush(color(C.brass if nid in route else C.faint))
                p.drawEllipse(QPointF(ox + x * k, oy + y * k), 1.15, 1.15)

            base = convo.baseline_id
            if base in self._pos:
                bx, by = self._pos[base]
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(color(C.ink_soft, 220), 0.9))
                p.drawEllipse(QPointF(ox + bx * k, oy + by * k), 2.1, 2.1)
            leaf = convo.view_leaf()
            if leaf in self._pos:
                lx, ly = self._pos[leaf]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(C.brass_hi))
                p.drawEllipse(QPointF(ox + lx * k, oy + ly * k), 1.7, 1.7)

        frame = self._viewport_frame(box)
        p.setBrush(color(C.brass, 24))
        p.setPen(QPen(color(C.brass, 225), 1))
        p.drawRect(frame)

        # move anchor: a small triangle in the top-right corner
        p.setPen(QPen(color(C.brass, 210), 1))
        p.setBrush(color(C.brass, 46))
        p.drawPolygon(
            QPolygonF([
                QPointF(box.right() - 11, box.top() + 1),
                QPointF(box.right() - 1, box.top() + 1),
                QPointF(box.right() - 1, box.top() + 11),
            ])
        )
        # resize: a corner bracket in the bottom-left
        p.setPen(QPen(color(C.ink_dim, 210), 1.2))
        p.drawLine(QPointF(box.left() + 1, box.bottom() - 9), QPointF(box.left() + 1, box.bottom() - 1))
        p.drawLine(QPointF(box.left() + 1, box.bottom() - 1), QPointF(box.left() + 9, box.bottom() - 1))

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


    def hover_box(self, nid: str) -> QRectF:
        """Where the preview card sits for `nid`: the side away from its dot."""
        plot = self.plot_rect()
        pt = self._pix.get(nid)
        above = pt is not None and pt.y() > plot.center().y()
        return QRectF(plot.left(), plot.top() + 8.0 if above else self.height() - 132.0, plot.width(), 62)

    def _draw_hover(self, p: QPainter) -> None:
        nid = self._hover
        if not nid or self.convo is None or nid not in self.convo.nodes:
            return
        node = self.convo.nodes[nid]
        box = self.hover_box(nid)
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
