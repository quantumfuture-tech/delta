"""The writing surface: calm, fast, and honest about what Enter will do."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.model import DEFAULT_MODEL, MODELS
from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import IconButton


MODEL_NAMES = {
    "deepseek-chat": ("DeepSeek Chat", "快"),
    "deepseek-reasoner": ("DeepSeek Reasoner", "深度"),
}


class ComposerEdit(QPlainTextEdit):
    submit = Signal()
    stop = Signal()
    focus_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._composing = False
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setFont(font(FONT_UI, 11))
        self.document().setDocumentMargin(3)
        self.setViewportMargins(0, 0, 0, 0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {C.ink}; "
            f"selection-background-color: {C.brass_deep}; selection-color: {C.ink}; padding: 0; }}"
        )

    def inputMethodEvent(self, event) -> None:
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        if not event.preeditString():
            self._composing = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if self._composing:
                super().keyPressEvent(event)
                return
            self.submit.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.stop.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class ComposerPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._focus = 0.0
        self._target = 0.0
        self._busy = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._anim_step)

    def set_focus(self, on: bool) -> None:
        self._target = 1.0 if on else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._focus)
        self._anim.setEndValue(self._target)
        self._anim.start()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.update()

    def _anim_step(self, value) -> None:
        self._focus = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(color(C.panel))
        base = QColor(C.line)
        hot = QColor(C.brass_deep)
        border = QColor(
            int(base.red() + (hot.red() - base.red()) * self._focus),
            int(base.green() + (hot.green() - base.green()) * self._focus),
            int(base.blue() + (hot.blue() - base.blue()) * self._focus),
        )
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(r, 15, 15)
        if self._focus > 0.02:
            p.setPen(QPen(color(C.brass, int(18 * self._focus)), 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 13, 13)
        p.end()


class ModelChip(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = DEFAULT_MODEL
        self._hover = False
        self.setFixedHeight(27)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("切换模型")
        self._resize()

    def _resize(self) -> None:
        fm = QFontMetrics(font(FONT_MONO, 8.6))
        self.setFixedWidth(fm.horizontalAdvance(self._model) + 42)

    def set_model(self, model: str) -> None:
        if model not in MODELS:
            model = DEFAULT_MODEL
        self._model = model
        self._resize()
        self.update()

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        menu = QMenu(self)
        for name in MODELS:
            label, hint = MODEL_NAMES[name]
            act = menu.addAction(f"{label}   ·   {hint}")
            act.setCheckable(True)
            act.setChecked(name == self._model)
            act.setData(name)
        menu.setMinimumWidth(230)
        menu.ensurePolished()
        pos = self.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 4))
        chosen = menu.exec(pos)
        if chosen is not None and chosen.data():
            self.changed.emit(str(chosen.data()))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(color(C.line_hi if self._hover else C.line), 1))
        p.setBrush(color(C.raised if self._hover else C.panel_hi))
        p.drawRoundedRect(r, 7, 7)
        is_reasoner = self._model == "deepseek-reasoner"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.brass if is_reasoner else C.sage))
        p.drawEllipse(QRectF(10, self.height() / 2 - 2.0, 4, 4))
        p.setPen(color(C.ink_dim))
        p.setFont(font(FONT_MONO, 8.6))
        p.drawText(QRectF(19, 0, self.width() - 38, self.height()), Qt.AlignmentFlag.AlignVCenter, self._model)
        draw_icon(p, "chevron_down", QRectF(self.width() - 18, self.height() / 2 - 6, 12, 12), color(C.muted), 1.4)
        p.end()


class SendButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "send"
        self._hover = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(46, 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._update_tip()

    def set_mode(self, mode: str) -> None:
        if mode not in ("send", "stop", "disabled"):
            mode = "send"
        self._mode = mode
        if mode == "stop":
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self._update_tip()
        self.update()

    def _update_tip(self) -> None:
        self.setToolTip({"send": "发送  (Enter)", "stop": "停止生成  (Esc)", "disabled": "先写点什么"}[self._mode])

    def _tick(self) -> None:
        self._phase += 0.14
        self.update()

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            if self._mode != "disabled":
                self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = self.rect().center()
        r = 19.0
        if self._mode == "stop":
            p.setPen(QPen(color(C.brass, 70), 1.5))
            p.setBrush(color("#1A1510"))
            p.drawEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            start = int((self._phase * 30) % 360)
            p.setPen(QPen(color(C.brass, 220), 2))
            p.drawArc(QRectF(c.x() - r - 3, c.y() - r - 3, 2 * r + 6, 2 * r + 6), -start * 16, -110 * 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass_hi))
            p.drawRoundedRect(QRectF(c.x() - 5.5, c.y() - 5.5, 11, 11), 2.6, 2.6)
        elif self._mode == "disabled":
            p.setPen(QPen(color(C.line), 1.2))
            p.setBrush(color(C.panel_hi))
            p.drawEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            draw_icon(p, "send", QRectF(c.x() - 9, c.y() - 9, 18, 18), color(C.faint), 1.7)
        else:
            bg = C.brass_hi if self._hover else C.brass
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(bg))
            p.drawEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            draw_icon(p, "send", QRectF(c.x() - 10, c.y() - 10, 20, 20), color("#12100B"), 1.8)
        p.end()


class Composer(QWidget):
    submit = Signal(str)
    stop = Signal()
    draft_changed = Signal(str)
    model_changed = Signal(str)
    height_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        self._note_kind = ""
        self.setMaximumWidth(890)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.note = QWidget(self)
        self.note.setFixedHeight(20)
        nl = QHBoxLayout(self.note)
        nl.setContentsMargins(18, 0, 18, 3)
        nl.setSpacing(6)
        self._note_dot = QLabel("●", self.note)
        self._note_dot.setStyleSheet(f"color:{C.brass}; background:transparent; font-size:6pt;")
        self._note_label = QLabel("", self.note)
        self._note_label.setStyleSheet(f"color:{C.muted}; background:transparent; font-size:8.6pt;")
        nl.addWidget(self._note_dot)
        nl.addWidget(self._note_label)
        nl.addStretch(1)
        self.note.setVisible(False)
        root.addWidget(self.note)

        self.panel = ComposerPanel(self)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(18, 12, 14, 11)
        pl.setSpacing(6)
        root.addWidget(self.panel)

        self.edit = ComposerEdit(self.panel)
        self.edit.setPlaceholderText("写下你的下一条…")
        self.edit.setFixedHeight(42)
        self.edit.submit.connect(self._submit)
        self.edit.stop.connect(self.stop)
        self.edit.focus_changed.connect(self.panel.set_focus)
        self.edit.textChanged.connect(self._on_text)
        pl.addWidget(self.edit)

        bottom = QWidget(self.panel)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.model_chip = ModelChip(bottom)
        self.model_chip.changed.connect(self.model_changed)
        bl.addWidget(self.model_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.context_label = QLabel("", bottom)
        self.context_label.setStyleSheet(f"color:{C.faint}; background:transparent; font-size:8.4pt;")
        bl.addWidget(self.context_label, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        self.hint = QLabel("Enter 发送 · Shift+Enter 换行", bottom)
        self.hint.setStyleSheet(f"color:{C.faint}; background:transparent; font-size:8.4pt;")
        bl.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignVCenter)
        self.send_button = SendButton(bottom)
        self.send_button.clicked.connect(self._on_send_clicked)
        bl.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(bottom)
        self._adjust_height()
        self.focus_input()

    # -- public API ----------------------------------------------------
    def focus_input(self) -> None:
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, text: str) -> None:
        self.edit.blockSignals(True)
        self.edit.setPlainText(text)
        self.edit.blockSignals(False)
        self._adjust_height()
        self._update_send()

    def clear(self) -> None:
        self.set_text("")
        self._adjust_height()
        self._update_send()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.panel.set_busy(busy)
        self.send_button.set_mode("stop" if busy else ("send" if self.text().strip() else "disabled"))
        self.hint.setText("Esc 停止生成 · 可继续输入" if busy else "Enter 发送 · Shift+Enter 换行")
        self._update_note()
        self.edit.setPlaceholderText(
            "正在生成…（可以先写下下一条）" if busy else self._placeholder_text()
        )

    def set_model(self, model: str) -> None:
        self.model_chip.set_model(model)

    def set_context(self, count: int, has_children: bool, children: int, busy_elsewhere: bool = False) -> None:
        self._context_count = count
        self._has_children = has_children
        self._child_count = children
        self._elsewhere = busy_elsewhere
        parts = [f"{count} 条上下文"] if count else []
        if has_children:
            parts.append(f"此处有 {children} 个后续")
        self.context_label.setText(" · ".join(parts))
        self._update_note()
        self._update_placeholder()

    def _placeholder_text(self) -> str:
        if getattr(self, "_has_children", False):
            return "从这条消息开一条新支…"
        return "写下你的下一条…"

    def _update_placeholder(self) -> None:
        if not self._busy:
            self.edit.setPlaceholderText(self._placeholder_text())

    def _update_note(self) -> None:
        text = ""
        kind = ""
        if getattr(self, "_elsewhere", False):
            text = "另一分支正在生成 · 这里的改动不会串线"
            kind = "live"
        elif getattr(self, "_has_children", False):
            text = "发送会从当前节点开一条新支，旧的都不会被改动"
            kind = "branch"
        if self._busy:
            text = "正在生成 · 已收到的内容会实时保留"
            kind = "live"
        if not text:
            self.note.setVisible(False)
            return
        self.note.setVisible(True)
        self._note_label.setText(text)
        if kind == "live":
            self._note_dot.setStyleSheet(f"color:{C.brass}; background:transparent; font-size:6pt;")
        else:
            self._note_dot.setStyleSheet(f"color:{C.sage}; background:transparent; font-size:6pt;")

    def _on_text(self) -> None:
        self._adjust_height()
        self._update_send()
        self.draft_changed.emit(self.text())

    def _update_send(self) -> None:
        if self._busy:
            self.send_button.set_mode("stop")
        else:
            self.send_button.set_mode("send" if self.text().strip() else "disabled")

    def _on_send_clicked(self) -> None:
        if self._busy:
            self.stop.emit()
        else:
            self._submit()

    def _submit(self) -> None:
        if self._busy:
            return
        text = self.text().strip()
        if not text:
            return
        self.submit.emit(text)

    def _adjust_height(self) -> None:
        doc = self.edit.document()
        doc.setTextWidth(max(100, self.edit.viewport().width()))
        h = doc.size().height()
        target = int(max(38, min(150, h + 6)))
        if self.edit.height() != target:
            self.edit.setFixedHeight(target)
            self.height_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.width() < 650:
            self.hint.setVisible(False)
        else:
            self.hint.setVisible(True)
        self._adjust_height()