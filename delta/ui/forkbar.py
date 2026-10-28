"""The visible fork: sibling messages as selectable, pinnable branches."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.model import Conversation, Node, one_line
from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import FlowLayout, fmt_count


def _preview(text: str) -> str:
    flat = one_line(text, 90)
    for token in ("**", "__", "```", "`", "> ", "#", "*"):
        flat = flat.replace(token, "")
    return flat.strip() or "（空）"


class BranchChip(QWidget):
    chosen = Signal()
    pin = Signal()

    def __init__(self, child: Node, *, index: int, active: bool, preferred: bool, descendants: int, parent=None) -> None:
        super().__init__(parent)
        self.child = child
        self.index = index
        self.active = active
        self.preferred = preferred
        self.descendants = descendants
        self._hover = False
        self._pin_hover = False
        self._text = ""
        self._preview_w = 42
        self._width = 0
        self._fm = QFontMetrics(font(FONT_UI, 9.3))
        self._fm_index = QFontMetrics(font(FONT_MONO, 8.0, 500, 0.5))
        self.setFixedHeight(33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.update_content()
        self.setToolTip(
            ("当前分支" if active else "切换到这一支")
            + (f" · 含 {descendants} 条后续" if descendants else "")
            + "\n点击右侧圆点可设/取消首选续写（只改动这一处）"
        )

    @property
    def display_text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        # FlowLayout is what decides the rows; make the hint available even
        # before the widget has been polished or shown.
        return QSize(max(1, self._width), 33)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def update_content(self) -> int:
        """Refresh the preview from the node and recompute the chip width.

        A streaming placeholder starts as "（待生成）"; when the sibling
        finishes, this is the entry point that turns it into the actual answer
        without waiting for a click.
        """
        self._text = _preview(self.child.text if self.child.text.strip() else "（待生成）")
        self._preview_w = min(154, max(42, self._fm.horizontalAdvance(self._text)))
        desc_w = 22 if self.descendants else 0
        new_width = 10 + 20 + 8 + self._preview_w + desc_w + 26
        if new_width != self._width or self.width() != new_width:
            self._width = new_width
            self.setFixedWidth(new_width)
        self.update()
        return new_width

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._pin_hover = False
        self.update()

    def mouseMoveEvent(self, e) -> None:
        self._pin_hover = e.position().x() > self.width() - 27
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        if e.position().x() > self.width() - 27:
            self.pin.emit()
        else:
            self.chosen.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.active:
            bg = color("#1C1811")
            border = color(C.brass, 210)
        elif self._hover:
            bg = color(C.raised)
            border = color(C.line_hi)
        else:
            bg = color(C.panel_hi)
            border = color(C.line)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 8, 8)
        if self.active:
            p.setPen(QPen(color(C.brass, 90), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 7, 7)

        p.setFont(font(FONT_MONO, 8.0, 500, 0.5))
        p.setPen(color(C.brass if self.active else C.faint))
        p.drawText(QRectF(10, 0, 20, self.height()), Qt.AlignmentFlag.AlignVCenter, f"{self.index:02d}")

        p.setFont(font(FONT_UI, 9.3))
        p.setPen(color(C.ink if self.active else C.ink_dim))
        avail = self.width() - 10 - 20 - 8 - (22 if self.descendants else 0) - 26
        text = self._fm.elidedText(self._text, Qt.TextElideMode.ElideRight, max(20, avail))
        p.drawText(QRectF(38, 0, avail, self.height()), Qt.AlignmentFlag.AlignVCenter, text)

        x = self.width() - 18
        if self.descendants:
            p.setFont(font(FONT_MONO, 7.8))
            p.setPen(color(C.faint))
            p.drawText(
                QRectF(x - 18, 0, 18, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                fmt_count(self.descendants),
            )
        pin_rect = QRectF(self.width() - 25, 7, 18, 19)
        if self.preferred:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass))
            p.drawEllipse(QRectF(pin_rect.center().x() - 3.0, pin_rect.center().y() - 3.0, 6, 6))
        else:
            p.setPen(QPen(color(C.line_hi if not self._pin_hover else C.ink_dim), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(pin_rect.center().x() - 3.2, pin_rect.center().y() - 3.2, 6.4, 6.4))
        p.end()


class ForkBar(QWidget):
    switch_requested = Signal(str, str)
    preferred_toggled = Signal(str, str)
    fork_here_requested = Signal(str)

    GUTTER = 44

    def __init__(self, convo: Conversation, parent_id: str, *, active_child: str | None, parent=None) -> None:
        super().__init__(parent)
        self.convo = convo
        self.parent_id = parent_id
        self.active_child = active_child
        self._chips: list[BranchChip] = []
        self._height_pending = False
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._deferred_sync)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        gutter = QWidget(self)
        gutter.setFixedWidth(self.GUTTER)
        gutter.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(gutter)

        holder = QWidget(self)
        self._holder_lay = QVBoxLayout(holder)
        self._holder_lay.setContentsMargins(0, 3, 10, 11)
        self._holder_lay.setSpacing(5)
        root.addWidget(holder, 1)

        top = QWidget(holder)
        top.setFixedHeight(20)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(1, 0, 3, 0)
        tl.setSpacing(7)
        self._title = QLabel(top)
        self._title.setFont(font(FONT_MONO, 8.0, 500, 0.9))
        self._title.setStyleSheet(f"color:{C.brass_dim}; background:transparent;")
        self._hint = QLabel(top)
        self._hint.setFont(font(FONT_UI, 8.5))
        self._hint.setStyleSheet(f"color:{C.faint}; background:transparent;")
        tl.addWidget(self._title)
        tl.addWidget(self._hint)
        tl.addStretch(1)
        self._holder_lay.addWidget(top)

        self._flow_host = QWidget(holder)
        self._flow = FlowLayout(self._flow_host, margin=0, hspacing=7, vspacing=7)
        self._flow_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._holder_lay.addWidget(self._flow_host)
        self.refresh(self.active_child)

    def _clear(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chips = []

    def refresh(self, active_child: str | None = None) -> None:
        self.active_child = active_child
        children = self.convo.children(self.parent_id)
        at_pointer = self.convo.pointer_id == self.parent_id
        self._clear()
        for i, child in enumerate(children):
            chip = BranchChip(
                child,
                index=i + 1,
                active=(child.id == active_child),
                preferred=(self.convo.nodes[self.parent_id].preferred_child == child.id),
                descendants=self.convo.descendant_count(child.id),
                parent=self._flow_host,
            )
            chip.chosen.connect(lambda cid=child.id: self.switch_requested.emit(self.parent_id, cid))
            chip.pin.connect(lambda cid=child.id: self.preferred_toggled.emit(self.parent_id, cid))
            self._flow.addWidget(chip)
            self._chips.append(chip)
        if active_child is None and not at_pointer:
            ghost = _GhostChip("从这再开一支", self._flow_host)
            ghost.clicked.connect(lambda: self.fork_here_requested.emit(self.parent_id))
            self._flow.addWidget(ghost)
        if at_pointer:
            self._title.setText("从这里继续")
            self._hint.setText("选一条；或直接在下方发送，开新支")
        elif len(children) > 1:
            self._title.setText(f"分支 · {len(children)}")
            self._hint.setText("圆点 = 首选")
        else:
            self._title.setText("继续")
            self._hint.setText("")
        self._flow.invalidate()
        self._sync_height()
        self.update()

    def refresh_texts(self) -> None:
        """Update previews/widths in place (completion, interruption, 401).

        If the children changed structurally, fall back to a full rebuild so
        the number of chips and active state stay correct.
        """
        children = {c.id: c for c in self.convo.children(self.parent_id)}
        if len(children) != len(self._chips) or any(c.child.id not in children for c in self._chips):
            self.refresh(self.active_child)
            return
        for chip in self._chips:
            chip.update_content()
        self._flow.invalidate()
        self._flow_host.updateGeometry()
        self._sync_height()
        self.updateGeometry()
        self.update()

    def chip_texts(self) -> list[str]:
        return [chip.display_text for chip in self._chips]

    def _content_width(self) -> int:
        w = self.width()
        if w < 200 and self.parentWidget() is not None and self.parentWidget().width() > 200:
            # During rebuild/insert the bar can still have its construction
            # width; the parent already knows the real column width.
            w = self.parentWidget().width()
        return w - self.GUTTER - 10

    def _sync_height(self) -> None:
        content_w = self._content_width()
        if content_w < 120:
            # Never lock a one-row height while the widget is still unlaid out.
            if not self._height_pending:
                self._height_pending = True
                self._height_timer.start(0)
            return
        self._height_timer.stop()
        self._height_pending = False
        self._flow.invalidate()
        h = self._flow.heightForWidth(content_w)
        row_h = max(33, h)
        if self._flow_host.height() != row_h:
            self._flow_host.setFixedHeight(row_h)
        total = 20 + 5 + row_h + 3 + 11
        if self.height() != total:
            self.setFixedHeight(total)
        self._height_pending = False
        self.updateGeometry()

    def _deferred_sync(self) -> None:
        self._height_pending = False
        self._sync_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(700, self.height() or 72)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        h = self._flow.heightForWidth(max(80, width - self.GUTTER - 10))
        return 20 + 5 + max(33, h) + 3 + 11

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 21.0
        p.setPen(QPen(color(C.line_soft), 1))
        p.drawLine(int(x), 0, int(x), self.height())
        y = 3 + 10
        p.setBrush(color(C.bg))
        p.setPen(QPen(color(C.brass_dim if self.active_child else C.muted), 1.2))
        p.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
        path = QPainterPath()
        path.moveTo(x + 3, y)
        path.cubicTo(x + 12, y, x + 12, y + 14, x + 32, y + 14)
        p.setPen(QPen(color(C.brass_deep if self.active_child else C.line_hi), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


class _GhostChip(QWidget):
    clicked = Signal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._hover = False
        self.setFixedSize(136, 33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(color(C.bg))
        pen = QPen(color(C.line_hi if self._hover else C.line), 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 8, 8)
        draw_icon(p, "plus", QRectF(10, 10, 13, 13), color(C.brass if self._hover else C.muted), 1.6)
        p.setPen(color(C.ink_dim if self._hover else C.muted))
        p.setFont(font(FONT_UI, 8.8))
        p.drawText(QRectF(29, 0, self.width() - 34, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()