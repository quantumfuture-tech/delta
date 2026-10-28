"""One message: a card on the spine; the layer strip above it handles alternates."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.model import Node
from .icons import draw_icon
from .rich import RichText
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import IconButton, PillButton, clock, fmt_count


REASONING_CSS = """
p { color: #9C9488; margin: 0 0 7px 0; }
li { color: #9C9488; }
strong { color: #C8BEAC; }
code { background-color: #171C24; color: #C9A36B; }
pre { background-color: #0E1218; }
"""


def error_title(kind: str | None) -> str:
    return {
        "auth": "密钥无效或未授权",
        "rate_limit": "请求太频繁，被限流了",
        "timeout": "等待响应超时",
        "connection": "连接中断",
        "server": "服务端出了错",
        "bad_request": "请求被拒绝",
        "protocol": "流数据异常",
        "config": "还没有配置密钥",
    }.get(kind or "", "生成失败")


def error_detail(kind: str | None, fallback: str = "") -> str:
    detail = {
        "auth": "检查 DEEPSEEK_API_KEY 后重启 Delta。",
        "rate_limit": "稍等片刻，或换一个模型再试。",
        "timeout": "已保留收到的内容。可以在这一层的 + 重新生成。",
        "connection": "已保留收到的内容。可以在这一层的 + 重新生成。",
        "server": "上游服务暂时不可用，已保留收到的内容。",
        "bad_request": "请求格式未被接受。",
        "protocol": "流式事件解析失败，已保留收到的内容。",
        "config": "在环境变量里设置 DEEPSEEK_API_KEY，然后重启。",
    }.get(kind or "", "")
    return detail or fallback


class MessagePanel(QWidget):
    def __init__(self, role: str, parent=None) -> None:
        super().__init__(parent)
        self.role = role
        self._hover = False
        self._pinned = False
        self._mouth: tuple[float, float] | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_hover(self, hover: bool) -> None:
        if self._hover != hover:
            self._hover = hover
            self.update()

    def set_mouth(self, mouth: tuple[float, float] | None) -> None:
        """Opening in the top edge where the layer's tab sits on this surface."""
        if self._mouth != mouth:
            self._mouth = mouth
            self.update()

    def set_pinned(self, pinned: bool) -> None:
        """This node sits on the shortcut chain: keep the accent, drop it to dark gold."""
        if self._pinned != pinned:
            self._pinned = pinned
            self.update()

    def _outline(self, rect: QRectF, radius: float) -> tuple[QPainterPath, QPainterPath]:
        if not self._mouth:
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            return path, path
        m0, m1 = self._mouth
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        m0 = max(left, min(m0, right))
        m1 = max(left + radius, min(m1, right))
        path = QPainterPath()
        path.moveTo(m1, top)
        path.lineTo(right - radius, top)
        path.quadTo(right, top, right, top + radius)
        path.lineTo(right, bottom - radius)
        path.quadTo(right, bottom, right - radius, bottom)
        path.lineTo(left + radius, bottom)
        path.quadTo(left, bottom, left, bottom - radius)
        if m0 <= left + 0.5:
            path.lineTo(left, top)
        else:
            path.lineTo(left, top + radius)
            path.quadTo(left, top, left + radius, top)
            path.lineTo(m0, top)
        fill = QPainterPath(path)
        fill.closeSubpath()
        return fill, path

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.role == "user":
            fill, edge = self._outline(rect, 13)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.user_bg))
            p.drawPath(fill)
            p.setPen(QPen(color(C.user_line), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(edge)
            p.setClipPath(fill)
            p.setPen(QPen(color(C.brass, 90) if self._pinned else color(C.brass_deep, 220), 2))
            p.drawLine(1, 18, 1, self.height() - 18)
        elif self._hover:
            fill, _edge = self._outline(rect, 11)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("#11161D", 220))
            p.drawPath(fill)
        p.end()


class StatusTag(QWidget):
    def __init__(self, text: str = "", parent=None, *, kind: str = "live") -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._phase = 0.0
        fm = QFontMetrics(font(FONT_MONO, 8.0, 500, 0.6))
        self._w = fm.horizontalAdvance(text) + 20
        self.setFixedSize(self._w, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_phase(self, phase: float) -> None:
        self._phase = phase
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        fm = QFontMetrics(font(FONT_MONO, 8.0, 500, 0.6))
        self._w = fm.horizontalAdvance(text) + 20
        self.setFixedWidth(self._w)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._kind == "live":
            base = QColor(C.brass)
            alpha = 120 + int(110 * (0.5 + 0.5 * math.sin(self._phase * 0.42)))
            c = QColor(base)
            c.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(2, self.height() / 2 - 2.5, 5, 5))
            p.setPen(color(C.brass_dim))
        else:
            p.setPen(color(C.danger))
            p.setBrush(color(C.danger))
            p.drawEllipse(QRectF(3, self.height() / 2 - 2.0, 4, 4))
            p.setPen(color(C.danger))
        p.setFont(font(FONT_MONO, 8.0, 500, 0.6))
        p.drawText(
            QRectF(12, 0, self.width() - 14, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )
        p.end()


class ReasoningHeader(QWidget):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._open = False
        self._label = "思考过程"
        self._count = 0
        self._hover = False
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, label: str, count: int, is_open: bool) -> None:
        self._label = label
        self._count = count
        self._open = is_open
        self.update()

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
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.raised, 120))
            p.drawRoundedRect(QRectF(0, 2, self.width(), self.height() - 4), 6, 6)
        draw_icon(p, "spark", QRectF(2, 8, 12, 12), color(C.brass_deep if not self._open else C.brass), 1.5)
        p.setPen(color(C.ink_dim if self._open else C.muted))
        p.setFont(font(FONT_UI, 9.2))
        text = f"{self._label} · {fmt_count(self._count)} 字"
        p.drawText(
            QRectF(20, 0, self.width() - 32, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        p.save()
        p.translate(self.width() - 13, self.height() / 2)
        if not self._open:
            p.rotate(-90)
        draw_icon(p, "chevron_down", QRectF(-7, -7, 14, 14), color(C.muted), 1.6)
        p.restore()
        p.end()


class MessageCard(QWidget):
    copy_requested = Signal(str)
    menu_requested = Signal(str, QPoint)

    GUTTER = 44

    def __init__(self, node: Node, *, current: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.node = node
        self.node_id = node.id
        self._current = current
        self._pinned = False
        self._hover = False
        self._phase = 0.0
        self._reasoning_open = False
        self._streaming_footer: QWidget | None = None
        self._pulse = QTimer(self)
        self._pulse.setInterval(70)
        self._pulse.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._build()
        self.refresh()
        if node.status == "streaming":
            self._start_pulse()

    # -- construction --------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._gutter = QWidget(self)
        self._gutter.setFixedWidth(self.GUTTER)
        self._gutter.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self._gutter)

        holder = QWidget(self)
        holder_lay = QVBoxLayout(holder)
        holder_lay.setContentsMargins(0, 0, 10, 8)
        holder_lay.setSpacing(0)
        root.addWidget(holder, 1)

        self.panel = MessagePanel(self.node.role, holder)
        self.panel_lay = QVBoxLayout(self.panel)
        if self.node.role == "user":
            self.panel_lay.setContentsMargins(20, 13, 20, 14)
        else:
            self.panel_lay.setContentsMargins(20, 13, 10, 12)
        self.panel_lay.setSpacing(0)
        holder_lay.addWidget(self.panel)

        header = QWidget(self.panel)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(7)
        self.role_label = QLabel(self.panel)
        self.role_label.setFont(font(FONT_MONO, 8.2, 600, 1.25))
        hl.addWidget(self.role_label)

        self.meta_label = QLabel(self.panel)
        self.meta_label.setFont(font(FONT_UI, 8.5))
        hl.addWidget(self.meta_label)
        hl.addStretch(1)

        self.status_tag = StatusTag("", self.panel)
        hl.addWidget(self.status_tag, 0, Qt.AlignmentFlag.AlignVCenter)

        self.actions = QWidget(self.panel)
        self.actions.setFixedHeight(26)
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        self.btn_copy = IconButton("copy", self.actions, size=26, icon_size=14, tip="复制原文", color_=C.muted)
        self._action_buttons = [self.btn_copy]
        for b in self._action_buttons:
            al.addWidget(b)
        self.actions.setFixedWidth(26 * len(self._action_buttons))
        hl.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignVCenter)
        self._set_actions_visible(False)

        self.panel_lay.addWidget(header)

        # reasoning (assistant only)
        self.reasoning_box = QWidget(self.panel)
        self.reasoning_lay = QVBoxLayout(self.reasoning_box)
        self.reasoning_lay.setContentsMargins(0, 3, 0, 0)
        self.reasoning_lay.setSpacing(2)
        self.reasoning_header = ReasoningHeader(self.reasoning_box)
        self.reasoning_body = RichText(self.reasoning_box, size_pt=9.4, css_extra=REASONING_CSS)
        self.reasoning_lay.addWidget(self.reasoning_header)
        self.reasoning_lay.addWidget(self.reasoning_body)
        self.reasoning_box.setVisible(False)
        self.reasoning_body.setVisible(False)
        self.panel_lay.addWidget(self.reasoning_box)

        self.body = RichText(self.panel)
        self.body.typing_started.connect(self._focus_composer)
        self.panel_lay.addWidget(self.body)

        self.footer = QWidget(self.panel)
        self.footer_lay = QHBoxLayout(self.footer)
        self.footer_lay.setContentsMargins(0, 8, 0, 0)
        self.footer_lay.setSpacing(8)
        self.footer.setVisible(False)
        self.panel_lay.addWidget(self.footer)

        self.btn_copy.clicked.connect(self._copy)
        self.reasoning_header.clicked.connect(self._toggle_reasoning)
        self.body.typing_started.connect(self._focus_composer)

    # -- content -------------------------------------------------------
    def refresh(self) -> None:
        n = self.node
        is_user = n.role == "user"
        model = (n.model or "deepseek-chat").replace("deepseek-", "DEEPSEEK ").upper()
        self.role_label.setText("YOU" if is_user else model)
        role_ink = (
            C.brass_dim if self._pinned else C.brass
        ) if is_user else (C.muted if self._pinned else C.ink_dim)
        self.role_label.setStyleSheet(f"color:{role_ink}; background:transparent;")
        self.meta_label.setText(clock(n.created_at))
        self.meta_label.setStyleSheet(f"color:{C.faint}; background:transparent;")

        streaming = n.status == "streaming"
        if streaming:
            self.status_tag.setVisible(True)
            self.status_tag.set_text("LIVE")
            self.status_tag._kind = "live"
            self.status_tag.update()
        elif n.status == "interrupted":
            self.status_tag.setVisible(True)
            self.status_tag.set_text("INTERRUPTED")
            self.status_tag._kind = "dead"
            self.status_tag.update()
        else:
            self.status_tag.setVisible(False)

        self.body.set_text(n.text, streaming=streaming)
        if not n.text and not streaming:
            self.body.set_text("（空响应）", streaming=False, force=True)

        has_reasoning = bool(n.reasoning)
        self.reasoning_box.setVisible(has_reasoning)
        if has_reasoning:
            if streaming and not n.text:
                self._reasoning_open = True
            elif streaming and n.text:
                self._reasoning_open = False
            self._apply_reasoning(streaming)
        self._rebuild_footer(streaming)
        self.updateGeometry()

    def _apply_reasoning(self, streaming: bool) -> None:
        count = len(self.node.reasoning)
        self.reasoning_header.set_state("正在思考" if streaming and not self.node.text else "思考过程", count, self._reasoning_open)
        self.reasoning_body.setVisible(self._reasoning_open)
        if self._reasoning_open:
            self.reasoning_body.set_text(self.node.reasoning, streaming=streaming)
        self.reasoning_header.update()

    def _toggle_reasoning(self) -> None:
        self._reasoning_open = not self._reasoning_open
        self.reasoning_body.setVisible(self._reasoning_open)
        if self._reasoning_open:
            self.reasoning_body.set_text(self.node.reasoning, streaming=self.node.status == "streaming")
        self._apply_reasoning(self.node.status == "streaming")
        self.updateGeometry()
        self.parentWidget() and self.parentWidget().updateGeometry()

    def update_stream(self) -> None:
        if self.node.status != "streaming":
            return
        self.body.set_text(self.node.text, streaming=True)
        if self.node.reasoning:
            self.reasoning_box.setVisible(True)
            self._apply_reasoning(True)
        if self._streaming_footer is not None:
            lbl = getattr(self, "_stream_label", None)
            if lbl is not None:
                lbl.setText(f"正在生成 · {fmt_count(len(self.node.text))} 字")
        self.updateGeometry()

    # -- footer --------------------------------------------------------
    def _clear_layout(self, lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild_footer(self, streaming: bool) -> None:
        n = self.node
        if streaming:
            if self._streaming_footer is None:
                self._clear_layout(self.footer_lay)
                self._streaming_footer = QWidget(self.footer)
                fl = QHBoxLayout(self._streaming_footer)
                fl.setContentsMargins(0, 0, 0, 0)
                fl.setSpacing(7)
                self._stream_dot = QLabel(self._streaming_footer)
                self._stream_dot.setFixedWidth(8)
                self._stream_dot.setText("·")
                self._stream_dot.setStyleSheet(f"color:{C.brass}; background:transparent; font-size:12pt;")
                self._stream_label = QLabel(f"正在生成 · {fmt_count(len(n.text))} 字", self._streaming_footer)
                self._stream_label.setStyleSheet(f"color:{C.muted}; background:transparent; font-size:9pt;")
                self._esc_label = QLabel("Esc 停止", self._streaming_footer)
                self._esc_label.setStyleSheet(f"color:{C.faint}; background:transparent; font-size:8.5pt;")
                fl.addWidget(self._stream_dot)
                fl.addWidget(self._stream_label)
                fl.addWidget(self._esc_label)
                fl.addStretch(1)
                self.footer_lay.addWidget(self._streaming_footer)
            self.footer.setVisible(True)
            return

        if self._streaming_footer is not None or n.status in ("interrupted", "error") or (n.role == "assistant" and not n.text and n.status == "complete"):
            self._streaming_footer = None
            self._clear_layout(self.footer_lay)
            if n.status == "interrupted":
                tag = QLabel("已中断", self.footer)
                tag.setStyleSheet(f"color:{C.danger}; background:transparent; font-size:9pt;")
                info = QLabel(f"已保留 {fmt_count(len(n.text))} 字", self.footer)
                info.setStyleSheet(f"color:{C.muted}; background:transparent; font-size:9pt;")
                self.footer_lay.addWidget(tag)
                self.footer_lay.addWidget(info)
                self.footer_lay.addStretch(1)
                self.footer.setVisible(True)
            elif n.error_kind:
                icon = QLabel("!", self.footer)
                icon.setFixedSize(16, 16)
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                icon.setStyleSheet(
                    f"color:{C.danger}; background:{C.danger_dim}; border-radius:8px; font-weight:700; font-size:9pt;"
                )
                title = QLabel(error_title(n.error_kind), self.footer)
                title.setStyleSheet(f"color:{C.danger}; background:transparent; font-size:9pt;")
                detail = QLabel(error_detail(n.error_kind), self.footer)
                detail.setStyleSheet(f"color:{C.muted}; background:transparent; font-size:9pt;")
                self.footer_lay.addWidget(icon)
                self.footer_lay.addWidget(title)
                self.footer_lay.addWidget(detail, 1)
                self.footer.setVisible(True)
            elif not n.text and n.role == "assistant":
                label = QLabel("模型返回了空内容", self.footer)
                label.setStyleSheet(f"color:{C.muted}; background:transparent; font-size:9pt;")
                self.footer_lay.addWidget(label)
                self.footer_lay.addStretch(1)
                self.footer.setVisible(True)
            else:
                self.footer.setVisible(False)
        else:
            self.footer.setVisible(False)

    # -- interactions --------------------------------------------------
    def _copy(self) -> None:
        QApplication.clipboard().setText(self.node.text)
        self.copy_requested.emit(self.node_id)

    def _focus_composer(self) -> None:
        self.window().composer.focus_input()  # type: ignore[attr-defined]

    def set_mouth(self, mouth: tuple[float, float] | None) -> None:
        """The layer's tab sits on this card; its opening is cut from the top edge."""
        self.panel.set_mouth(mouth)

    def set_pinned(self, pinned: bool) -> None:
        """On the shortcut chain: the card's accents drop to dark gold."""
        if self._pinned == pinned:
            return
        self._pinned = pinned
        self.panel.set_pinned(pinned)
        self.refresh()

    def set_current(self, current: bool) -> None:
        if self._current == current:
            return
        self._current = current
        self.update()

    def _start_pulse(self) -> None:
        if not self._pulse.isActive():
            self._pulse.start()

    def _tick(self) -> None:
        self._phase += 1.0
        self.status_tag.set_phase(self._phase)
        self.update(0, 0, self.GUTTER, self.height())
        if self.node.status != "streaming":
            self._pulse.stop()

    @property
    def action_buttons(self):
        return tuple(self._action_buttons)

    def _set_actions_visible(self, visible: bool) -> None:
        for button in self._action_buttons:
            button.setVisible(visible)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.panel.set_hover(True)
        self._set_actions_visible(True)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.panel.set_hover(False)
        self._set_actions_visible(False)
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(self.node_id, event.globalPos())
        event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 21.0
        p.setPen(QPen(color(C.line_soft), 1))
        p.drawLine(int(x), 0, int(x), self.height())
        cy = 30.0
        streaming = self.node.status == "streaming"
        if streaming:
            glow = 3.0 + 1.8 * (0.5 + 0.5 * math.sin(self._phase * 0.42))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass, 30))
            p.drawEllipse(QRectF(x - 7 - glow / 2, cy - 7 - glow / 2, 14 + glow, 14 + glow))
        if self._current:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass, 28))
            p.drawEllipse(QRectF(x - 8, cy - 8, 16, 16))
            p.setPen(QPen(color(C.brass), 1.5))
            p.setBrush(color(C.brass))
            p.drawEllipse(QRectF(x - 3, cy - 3, 6, 6))
        elif self.node.role == "user":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass_deep if not self._hover else C.brass))
            p.drawRoundedRect(QRectF(x - 2.6, cy - 2.6, 5.2, 5.2), 1.4, 1.4)
        else:
            p.setPen(QPen(color(C.muted if not self._hover else C.ink_dim), 1.3))
            p.setBrush(color(C.bg))
            p.drawEllipse(QRectF(x - 3.4, cy - 3.4, 6.8, 6.8))
            if streaming:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(C.brass))
                p.drawEllipse(QRectF(x - 1.2, cy - 1.2, 2.4, 2.4))
        p.end()