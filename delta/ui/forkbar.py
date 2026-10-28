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
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(
        self,
        child: Node,
        *,
        index: int,
        active: bool,
        descendants: int,
        locked: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.child = child
        self.index = index
        self.active = active
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
        self.update_content()
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        bits = [one_line(self.child.text, 70) or "（空）"]
        if self.descendants:
            bits.append(f"含 {self.descendants} 条后续")
        if self.locked:
            bits.append("这一层在捷径链上 · 点它查看解锁")
        else:
            bits.append("点击切换 · 拖动排序 · 右侧 × 关闭")
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
        new_width = 10 + 20 + 8 + preview_w + desc_w + 26
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
        if event.position().x() > self.width() - 27:
            self.closed.emit(self.child.id)
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
        # the close affordance lives in the slot the pin used to occupy
        col = C.ink_dim if (self._hover and not self.locked) else C.line_hi if self._hover else C.faint
        p.setPen(QPen(color(col), 1.25))
        cx, cy = self.width() - 16, self.height() / 2
        p.drawLine(int(cx - 3.2), int(cy - 3.2), int(cx + 3.2), int(cy + 3.2))
        p.drawLine(int(cx - 3.2), int(cy + 3.2), int(cx + 3.2), int(cy - 3.2))
        p.end()


class AddChip(QWidget):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover = False
        self.setFixedSize(64, 33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("在这一层新建一支")

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
        p.setBrush(color(C.bg))
        p.setPen(QPen(color(C.line_hi if self._hover else C.line), 1, Qt.PenStyle.DashLine))
        p.drawRoundedRect(r, 8, 8)
        draw_icon(p, "plus", QRectF(8, 10, 13, 13), color(C.brass if self._hover else C.muted), 1.6)
        p.setPen(color(C.ink_dim if self._hover else C.muted))
        p.setFont(font(FONT_UI, 8.8))
        p.drawText(
            QRectF(26, 0, self.width() - 31, self.height()),
            Qt.AlignmentFlag.AlignVCenter,
            "新支",
        )
        p.end()


class _HScroll(QScrollArea):
    def wheelEvent(self, event) -> None:
        pixel = event.pixelDelta()
        bar = self.horizontalScrollBar()
        if pixel.x():
            bar.setValue(bar.value() - pixel.x())
            event.accept()
            return
        angle = event.angleDelta()
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) or angle.x():
            bar.setValue(bar.value() - (angle.x() or angle.y()))
            event.accept()
            return
        super().wheelEvent(event)


class LayerBar(QWidget):
    """One layer's entrance.  The lock is checked by the conversation, not here."""

    activate_requested = Signal(str, str)
    new_requested = Signal(str)
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.convo = convo
        self.parent_id = parent_id
        self.active_child = active_child
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
        self._confirm_cancel = PillButton("取消", self._confirm)
        self._confirm_ok = PillButton("抬基准", self._confirm, accent=True)
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
        self._enter = QShortcut(QKeySequence("Return"), self)
        self._enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._enter.activated.connect(self._enter_pressed)

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
                descendants=self.convo.descendant_count(child.id),
                locked=layer.is_locked,
                parent=self._host,
            )
            chip.activated.connect(self._on_activate)
            chip.closed.connect(self._on_close)
            chip.drag_released.connect(self._on_drag)
            self._row.addWidget(chip)
            self._chips.append(chip)
        self._row.addStretch(1)
        self._host.setMinimumWidth(self._content_width())
        self._title.setText(self._title_text(layer, total))
        self._hint.setText(self._hint_text(layer, total))
        if active_child:
            for chip in self._chips:
                if chip.child.id == active_child:
                    self._scroll.ensureWidgetVisible(chip, 8, 0)
                    break
        self.update()

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
        return f"分支 · {total}" if total > 1 else "继续"

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
        if self.convo.is_locked(self.parent_id):
            self._show_confirm()
            return
        self.new_requested.emit(self.parent_id)

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

    def _enter_pressed(self) -> None:
        if self._confirm.isVisible():
            self._confirm_unlock()

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
