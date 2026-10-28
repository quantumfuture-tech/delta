"""The entrance to one layer: a single row of sibling tabs.

The layer is the object; this widget is only one way in.  Every gesture here
(activate, new, close, reorder) is emitted as an intent and executed by the
conversation, which checks the layer lock.  A locked layer never reaches the
model: it only opens the inline "raise the baseline" confirmation.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import slots
from ..core.model import Conversation, Node, one_line
from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import PillButton, fmt_count


def _preview(text: str) -> str:
    flat = one_line(text, 90)
    for token in ("**", "__", "```", "`", "> ", "#", "*"):
        flat = flat.replace(token, "")
    return flat.strip() or "（空）"


class TabChip(QWidget):
    activated = Signal(str)
    closed = Signal(str)
    drag_released = Signal(str, QPoint)
    slot_menu_requested = Signal(str, QPoint)
    slot_requested = Signal(str)

    def __init__(
        self,
        child: Node,
        *,
        index: int,
        active: bool,
        open: bool,
        descendants: int,
        locked: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.child = child
        self.index = index
        self.active = active
        self.open = open
        self.descendants = descendants
        self.locked = locked
        self._hover = False
        self._press = False
        self._moved = False
        self._down = QPoint()
        self._text = ""
        self._width = 0
        self._fm = QFontMetrics(font(FONT_UI, 9.3))
        self.setFixedHeight(33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.slot_menu_requested.emit(self.child.id, self.mapToGlobal(pos))
        )
        self.update_content()
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        bits = [one_line(self.child.text, 70) or "（空）"]
        if self.descendants:
            bits.append(f"含 {self.descendants} 条后续")
        if self.locked:
            bits.append("这一层在捷径链上 · 点它查看解锁")
        else:
            bits.append("点击切换 · 拖动排序 · + 打开这一支的槽 · × 关闭")
        return "\n".join(bits)

    @property
    def display_text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        return QSize(max(1, self._width), 33)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def update_content(self) -> int:
        self._text = _preview(self.child.text if self.child.text.strip() else "（待生成）")
        preview_w = min(154, max(42, self._fm.horizontalAdvance(self._text)))
        desc_w = 22 if self.descendants else 0
        new_width = 10 + 20 + 8 + preview_w + desc_w + 52
        if new_width != self._width or self.width() != new_width:
            self._width = new_width
            self.setFixedWidth(new_width)
        self.setToolTip(self._tooltip())
        self.update()
        return new_width

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = True
        self._moved = False
        self._down = event.globalPosition().toPoint()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press and not self._moved:
            if (event.globalPosition().toPoint() - self._down).manhattanLength() > 14:
                self._moved = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._press:
            return
        self._press = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._moved:
            self._moved = False
            self.drag_released.emit(self.child.id, event.globalPosition().toPoint())
            return
        if not self.rect().contains(event.position().toPoint()):
            return
        x = event.position().x()
        if x > self.width() - 27:
            self.closed.emit(self.child.id)
        elif x > self.width() - 47:
            self.slot_requested.emit(self.child.id)
        else:
            self.activated.emit(self.child.id)

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
        p.setPen(color(C.brass if (self.active or self.open) else C.faint))
        p.drawText(QRectF(10, 0, 20, self.height()), Qt.AlignmentFlag.AlignVCenter, f"{self.index:02d}")

        p.setFont(font(FONT_UI, 9.3))
        p.setPen(color(C.ink if self.active else C.ink_dim))
        avail = self.width() - 10 - 20 - 8 - (22 if self.descendants else 0) - 52
        text = self._fm.elidedText(self._text, Qt.TextElideMode.ElideRight, max(20, avail))
        p.drawText(QRectF(38, 0, avail, self.height()), Qt.AlignmentFlag.AlignVCenter, text)

        if self.descendants:
            p.setFont(font(FONT_MONO, 7.8))
            p.setPen(color(C.faint))
            p.drawText(
                QRectF(self.width() - 70, 0, 18, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                fmt_count(self.descendants),
            )
        # explicit slot opener (left) and close (right), both on the tab
        cy = self.height() / 2
        col = C.ink_dim if self._hover else C.faint
        p.setPen(QPen(color(col), 1.25))
        px = self.width() - 34
        p.drawLine(int(px - 3), int(cy), int(px + 3), int(cy))
        p.drawLine(int(px), int(cy - 3), int(px), int(cy + 3))
        col = C.ink_dim if (self._hover and not self.locked) else C.line_hi if self._hover else C.faint
        p.setPen(QPen(color(col), 1.25))
        cx = self.width() - 16
        p.drawLine(int(cx - 3.2), int(cy - 3.2), int(cx + 3.2), int(cy + 3.2))
        p.drawLine(int(cx - 3.2), int(cy + 3.2), int(cx + 3.2), int(cy - 3.2))
        if self.open:
            p.setPen(QPen(color(C.brass), 2))
            p.drawLine(8, int(self.height()) - 2, self.width() - 8, int(self.height()) - 2)
            p.drawLine(7, 8, 7, int(self.height()) - 8)
        p.end()

    def set_open(self, open: bool) -> None:
        if self.open == open:
            return
        self.open = open
        self.update()


class AddChip(QWidget):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover = False
        self.open = False
        self.setFixedSize(64, 33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("打开这一层的末尾槽")

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(color(C.brass if self.open else C.bg))
        p.setPen(
            QPen(
                color(C.brass if self.open else C.line_hi if self._hover else C.line),
                1.4 if self.open else 1,
                Qt.PenStyle.SolidLine if self.open else Qt.PenStyle.DashLine,
            )
        )
        p.drawRoundedRect(r, 8, 8)
        draw_icon(
            p,
            "plus",
            QRectF(8, 10, 13, 13),
            color(C.bg if self.open else C.brass if self._hover else C.muted),
            1.6,
        )
        p.setPen(color(C.bg if self.open else C.ink_dim if self._hover else C.muted))
        p.setFont(font(FONT_UI, 8.8))
        p.drawText(
            QRectF(26, 0, self.width() - 31, self.height()),
            Qt.AlignmentFlag.AlignVCenter,
            "新支",
        )
        if self.open:
            p.setPen(QPen(color(C.brass), 2))
            p.drawLine(8, int(self.height()) - 2, self.width() - 8, int(self.height()) - 2)
        p.end()

    def set_open(self, open: bool) -> None:
        if self.open == open:
            return
        self.open = open
        self.update()


class _ConfirmButton(PillButton):
    """A focusable pill: Return/Enter activates this button, not the bar."""

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class _HScroll(QScrollArea):
    def wheelEvent(self, event) -> None:
        # The strip always consumes the wheel: vertical wheel, horizontal
        # wheel and Shift+wheel all scroll the tabs.  At either end the event
        # is still consumed, so it never falls through to the transcript.
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        delta = pixel.x() or pixel.y() or angle.x() or angle.y()
        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
        event.accept()


class LayerBar(QWidget):
    """One layer's entrance.  The lock is checked by the conversation, not here."""

    activate_requested = Signal(str, str)
    tail_slot_requested = Signal(str)
    slot_requested = Signal(str, str, str)
    close_requested = Signal(str, str)
    reorder_requested = Signal(str, str, int)
    baseline_requested = Signal(str)

    GUTTER = 44
    ROW_H = 33

    def __init__(
        self,
        convo: Conversation,
        parent_id: str,
        *,
        active_child: str | None = None,
        open_base: str | None = None,
        open_tail: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.convo = convo
        self.parent_id = parent_id
        self.active_child = active_child
        self._open_base = open_base
        self._open_tail = open_tail
        self._chips: list[TabChip] = []
        self._setup()
        self.refresh(active_child)

    # -- construction --------------------------------------------------
    def _setup(self) -> None:
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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

        self._body = QWidget(holder)
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(7)
        self._scroll = _HScroll(self._body)
        self._scroll.setFixedHeight(self.ROW_H)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        self._scroll.viewport().setAutoFillBackground(False)
        self._host = QWidget()
        self._host.setStyleSheet("background:transparent;")
        self._row = QHBoxLayout(self._host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(7)
        self._scroll.setWidget(self._host)
        self._add = AddChip(self._body)
        self._add.clicked.connect(self._on_add)
        body.addWidget(self._scroll, 1)
        body.addWidget(self._add, 0)
        self._holder_lay.addWidget(self._body)

        self._confirm = QWidget(holder)
        cl = QHBoxLayout(self._confirm)
        cl.setContentsMargins(1, 0, 0, 0)
        cl.setSpacing(8)
        self._confirm_label = QLabel(self._confirm)
        self._confirm_label.setFont(font(FONT_UI, 8.8))
        self._confirm_label.setStyleSheet(f"color:{C.ink_dim}; background:transparent;")
        self._confirm_label.setTextFormat(Qt.TextFormat.PlainText)
        self._confirm_cancel = _ConfirmButton("取消", self._confirm)
        self._confirm_ok = _ConfirmButton("抬基准", self._confirm, accent=True)
        # PillButton is deliberately NoFocus in dialogs; the two-step unlock
        # is the one place where Tab + Enter must work.
        self._confirm_cancel.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._confirm_ok.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._confirm_cancel.clicked.connect(self._hide_confirm)
        self._confirm_ok.clicked.connect(self._confirm_unlock)
        cl.addWidget(self._confirm_label, 1)
        cl.addWidget(self._confirm_cancel, 0)
        cl.addWidget(self._confirm_ok, 0)
        self._confirm.hide()
        self._holder_lay.addWidget(self._confirm)

        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc.activated.connect(self._hide_confirm)

    # -- state ---------------------------------------------------------
    def _clear(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._chips = []

    def refresh(self, active_child: str | None = None) -> None:
        self.active_child = active_child
        self._hide_confirm()
        self._clear()
        layer = self.convo.layer(self.parent_id)
        birth = [n.id for n in layer.children]
        total = len(layer.tabs)
        for child in layer.tabs:
            chip = TabChip(
                child,
                index=birth.index(child.id) + 1,
                active=(child.id == active_child),
                open=(child.id == self._open_base),
                descendants=self.convo.descendant_count(child.id),
                locked=layer.is_locked,
                parent=self._host,
            )
            chip.activated.connect(self._on_activate)
            chip.closed.connect(self._on_close)
            chip.drag_released.connect(self._on_drag)
            chip.slot_menu_requested.connect(self._tab_menu)
            chip.slot_requested.connect(self._on_chip_slot)
            self._row.addWidget(chip)
            self._chips.append(chip)
        self._row.addStretch(1)
        self._add.set_open(self._open_tail)
        self._host.setMinimumWidth(self._content_width())
        self._title.setText(self._title_text(layer, total))
        self._hint.setText(self._hint_text(layer, total))
        if active_child:
            for chip in self._chips:
                if chip.child.id == active_child:
                    self._scroll.ensureWidgetVisible(chip, 8, 0)
                    break
        self.update()

    def set_open(self, open_base: str | None, open_tail: bool) -> None:
        self._open_base = open_base
        self._open_tail = open_tail
        for chip in self._chips:
            chip.set_open(chip.child.id == open_base)
        self._add.set_open(open_tail)

    def _content_width(self) -> int:
        widths = [chip.sizeHint().width() for chip in self._chips]
        return sum(widths) + max(0, len(widths) - 1) * 7

    def _title_text(self, layer, total: int) -> str:
        if layer.is_locked:
            return f"锁 · {total} 支"
        if total == 0:
            return "继续"
        if total == 1 and self.active_child is None:
            return "未选"
        return f"分支 · {total}"

    def _hint_text(self, layer, total: int) -> str:
        if layer.is_locked:
            return "这一层在捷径链上 · 点一下查看解锁"
        if total > 1:
            return "点击切换 · 拖动排序"
        if total == 0:
            return "点 + 接一支"
        return ""

    def refresh_texts(self) -> None:
        children = {c.id for c in self.convo.children(self.parent_id)}
        if len(children) != len(self._chips) or any(c.child.id not in children for c in self._chips):
            self.refresh(self.active_child)
            return
        for chip in self._chips:
            chip.update_content()
        self._host.setMinimumWidth(self._content_width())
        self._scroll.updateGeometry()
        self.updateGeometry()
        self.update()

    def chip_texts(self) -> list[str]:
        return [chip.display_text for chip in self._chips]

    # -- gestures ------------------------------------------------------
    def _on_activate(self, child_id: str) -> None:
        if self.convo.is_locked(self.parent_id):
            self._show_confirm()
            return
        self.activate_requested.emit(self.parent_id, child_id)

    def _on_close(self, child_id: str) -> None:
        if self.convo.is_locked(self.parent_id):
            self._show_confirm()
            return
        self.close_requested.emit(self.parent_id, child_id)

    def _on_add(self) -> None:
        # Opening a slot is a draft action, not a layer write: allowed even locked.
        self.tail_slot_requested.emit(self.parent_id)

    def _on_chip_slot(self, child_id: str) -> None:
        key = slots.SlotKey(self.parent_id, child_id)
        modes = slots.allowed_modes(self.convo, key)
        if modes:
            self.slot_requested.emit(self.parent_id, child_id, modes[0])

    def _tab_menu(self, child_id: str, global_pos: QPoint) -> None:
        key = slots.SlotKey(self.parent_id, child_id)
        modes = slots.allowed_modes(self.convo, key)
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{C.panel_hi}; color:{C.ink_dim}; border:1px solid {C.line}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{C.raised}; color:{C.ink}; }}"
        )
        for mode in modes:
            menu.addAction(
                slots.MODE_LABEL[mode],
                lambda *_, m=mode: self.slot_requested.emit(self.parent_id, child_id, m),
            )
        menu.exec(global_pos)

    def _on_drag(self, child_id: str, global_pos: QPoint) -> None:
        if self.convo.is_locked(self.parent_id):
            self._show_confirm()
            return
        if not self.rect().contains(self.mapFromGlobal(global_pos)):
            return
        vp = self._scroll.viewport()
        pos = vp.mapFromGlobal(global_pos)
        ids = [chip.child.id for chip in self._chips]
        if child_id not in ids:
            return
        target = len(ids)
        for i, chip in enumerate(self._chips):
            centre = chip.mapTo(vp, chip.rect().center())
            if pos.x() < centre.x():
                target = i
                break
        old = ids.index(child_id)
        if target > old:
            target -= 1
        if target != old:
            self.reorder_requested.emit(self.parent_id, child_id, target)

    # -- the two-step unlock ------------------------------------------
    def _show_confirm(self) -> None:
        parent = self.convo.nodes.get(self.parent_id)
        label = one_line(parent.text, 20) if parent is not None else "对话开头"
        self._confirm_label.setText(f"这一层在捷径链上；把基准抬到「{label or '对话开头'}」？")
        self._body.hide()
        self._confirm.show()
        self._confirm_ok.setFocus(Qt.FocusReason.OtherFocusReason)

    def _hide_confirm(self) -> None:
        if self._confirm.isVisible():
            self._confirm.hide()
            self._body.show()

    def _confirm_unlock(self) -> None:
        self._hide_confirm()
        self.baseline_requested.emit(self.parent_id)

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
