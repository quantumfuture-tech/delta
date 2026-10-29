"""The turn rail: one short mark per turn on the right edge of the transcript.

Ported from the harness component that does the same job (its `TurnNavigator`):
a fixed 10px pitch, 12x2 marks, the current turn drawn 20x2 in the strongest
ink, and a 300x100 preview card on the left of the mark under the pointer.

Two differences, both because Delta's model is different rather than better:

* every turn is already in memory, so a mark never has to page history in —
  clicking one is a plain scroll and the mark never shows a "busy" pulse;
* there is no proportional compression.  A fixed pitch keeps the marks a ruler
  of turns; when the ladder outgrows the frame it scrolls inside it.

Colour is Delta's own.  The rail is navigation chrome, so it speaks the grey
"structure" channel; the mark under the pointer borrows the brass accent, and
the turn you are in is ink.  Gold stays reserved for the route.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QTextLayout
from PySide6.QtWidgets import QWidget

from .theme import C, FONT_CJK, FONT_MONO, color, font

# -- geometry, taken from the reference component ---------------------------
PITCH = 10
INSET = 6
MARK_H = 2
W_IDLE = 12
W_HOVER = 18
W_ACTIVE = 20
FRAME_W = 28
MAX_H = 420
EDGE_GAP = 20
FADE = 24

CARD_W = 300
CARD_MAX_H = 100
CARD_GAP = 10
CARD_PAD_X = 12
CARD_PAD_Y = 10
CARD_NUM_W = 26
SHADOW = 8

PROMPT_PREVIEW = 50
RESPONSE_PREVIEW = 120

# Delta's own channels: structure grey for the ladder, brass for the mark under
# the pointer, ink for the turn being read.  Gold is never used here — it still
# means "the route" everywhere else in the window.
IDLE = color(C.faint, 165)
HOVER = color(C.brass)
ACTIVE = color(C.ink)


@dataclass(frozen=True)
class Turn:
    """One turn: a prompt, the answer it drew, and the row to scroll to."""

    node_id: str
    prompt: str
    response: str


def preview(text: str | None, limit: int) -> str:
    """Collapse whitespace and clip to `limit` characters, one ellipsis."""
    body = " ".join((text or "").split())
    if len(body) > limit:
        return body[: limit - 1].rstrip() + "…"
    return body


def _wrap(text: str, font_: QFont, width: int, max_lines: int) -> list[str]:
    """Break `text` into at most `max_lines` lines using Qt's own line breaker.

    Qt splits CJK between characters and latin between words, which is exactly
    what a hand-rolled greedy wrapper gets wrong (it tends to strand a short
    latin token on a line of its own before a long Chinese paragraph).
    """
    layout = QTextLayout(text, font_)
    layout.beginLayout()
    lines: list[str] = []
    while len(lines) < max_lines:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        start = line.textStart()
        lines.append(text[start : start + line.textLength()].rstrip())
    layout.endLayout()
    return [line for line in lines if line]


class TurnCard(QWidget):
    """The hover preview: which turn, what was asked, how the answer opened."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._index = 0
        self._prompt = ""
        self._response_lines: list[str] = []
        self._prompt_h = 16
        self._line_h = 14
        self.resize(CARD_W + 2 * SHADOW, CARD_MAX_H + 2 * SHADOW)
        self.hide()

    def set_turn(self, index: int, prompt: str, response: str) -> None:
        self._index = index
        inner = CARD_W - 2 * CARD_PAD_X
        prompt_font = font(FONT_CJK, 10.0, 600)
        response_font = font(FONT_CJK, 9.0)
        fm_prompt = QFontMetrics(prompt_font)
        fm_response = QFontMetrics(response_font)
        self._prompt = fm_prompt.elidedText(
            preview(prompt, PROMPT_PREVIEW) or f"第 {index + 1} 轮",
            Qt.TextElideMode.ElideRight,
            inner - CARD_NUM_W,
        )
        self._prompt_h = fm_prompt.height()
        body = preview(response, RESPONSE_PREVIEW).rstrip("。，、；：,.;: ")
        self._response_lines = _wrap(body, response_font, inner, 3) if body else []
        if body:
            # an answer cut by the card must say so: trim the last line and put
            # an ellipsis in it — a dangling "。" on its own line is not a cut
            joined = "".join(self._response_lines).replace(" ", "")
            if joined != body.replace(" ", ""):
                lines = list(self._response_lines)
                last = (lines[-1] if lines else "").rstrip("。，、；： ")
                while last and fm_response.horizontalAdvance(last + "…") > inner:
                    last = last[:-1]
                if lines:
                    lines[-1] = (last + "…") if last else "…"
                else:
                    lines = ["…"]
                self._response_lines = lines
        self._line_h = fm_response.lineSpacing()
        height = CARD_PAD_Y * 2 + self._prompt_h
        if self._response_lines:
            height += 4 + len(self._response_lines) * self._line_h
        height = max(54, min(CARD_MAX_H, height))
        self.resize(CARD_W + 2 * SHADOW, height + 2 * SHADOW)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        surface = QRectF(SHADOW, SHADOW, self.width() - 2 * SHADOW, self.height() - 2 * SHADOW)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color("#05070A", 130))
        p.drawRoundedRect(surface.adjusted(-2, 0, 2, 4), 12, 12)
        p.setBrush(color(C.panel_hi))
        p.setPen(QPen(color(C.line), 1))
        p.drawRoundedRect(surface, 10, 10)

        # the brass tick faces the rail, so the card reads as belonging to that mark
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.brass_deep))
        p.drawRoundedRect(
            QRectF(surface.right() - 2, surface.top() + 10, 2, surface.height() - 20), 1, 1
        )

        left = surface.left() + CARD_PAD_X
        top = surface.top() + CARD_PAD_Y
        p.setFont(font(FONT_MONO, 8.0))
        p.setPen(color(C.brass_deep))
        p.drawText(
            QRectF(left, top, CARD_NUM_W, self._prompt_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self._index + 1:02d}",
        )

        p.setFont(font(FONT_CJK, 10.0, 600))
        p.setPen(color(C.ink))
        p.drawText(
            QRectF(left + CARD_NUM_W, top, surface.width() - 2 * CARD_PAD_X - CARD_NUM_W, self._prompt_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._prompt,
        )

        if self._response_lines:
            p.setFont(font(FONT_CJK, 9.0))
            p.setPen(color(C.muted))
            y = top + self._prompt_h + 4
            for line in self._response_lines:
                p.drawText(
                    QRectF(left, y, surface.width() - 2 * CARD_PAD_X, self._line_h),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
                y += self._line_h
        p.end()


class TurnRail(QWidget):
    """A fixed-pitch ladder of turns.  Hover previews, click scrolls, nothing else."""

    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._turns: list[Turn] = []
        self._active = -1
        self._hover = -1
        self._scroll = 0.0
        self._overflow = 0.0
        self._natural = 0.0
        self._card = TurnCard(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    # -- state ---------------------------------------------------------
    def set_state(self, turns: list[Turn], active: int) -> None:
        if turns != self._turns:
            self._turns = list(turns)
            self._scroll = 0.0
        self._active = active
        if len(self._turns) < 2 and self._hover >= 0:
            self._hover = -1
            self._card.hide()
        self.update()

    def place(self, viewport_w: int, viewport_h: int, column_right: int) -> None:
        """Sit in the gutter right of the reading column, centred on the band."""
        count = len(self._turns)
        natural = (count - 1) * PITCH + 2 * INSET if count else 0.0
        self._natural = float(natural)
        gutter = viewport_w - column_right
        if count < 2 or gutter < FRAME_W + 16 or viewport_h < 160:
            self._card.hide()
            self.hide()
            return
        gap = max(8, min(EDGE_GAP, gutter - FRAME_W - 8))
        frame_h = min(natural, max(0.0, viewport_h - 64), MAX_H)
        self._overflow = max(0.0, natural - frame_h)
        self._scroll = max(0.0, min(self._scroll, self._overflow))
        self.setGeometry(
            int(viewport_w - gap - FRAME_W),
            int((viewport_h - frame_h) / 2),
            FRAME_W,
            int(frame_h),
        )
        self.raise_()
        self.show()
        self._reveal_active()
        if self._hover >= 0:
            self._show_card(self._hover)

    def hideEvent(self, event) -> None:
        self._card.hide()
        super().hideEvent(event)

    # -- geometry ------------------------------------------------------
    def _mark_y(self, index: int) -> float:
        return INSET + index * PITCH - self._scroll

    def _index_at(self, y: float) -> int:
        if not self._turns:
            return -1
        offset = y - INSET + self._scroll
        hit = int(round(offset / PITCH))
        return max(0, min(len(self._turns) - 1, hit))

    def _reveal_active(self) -> None:
        """Keep the current mark inside the frame while the pointer is elsewhere."""
        if self._active < 0 or self._hover >= 0 or self.height() <= 0 or self._overflow <= 0:
            return
        y = self._mark_y(self._active)
        if FADE <= y <= self.height() - FADE:
            return
        target = INSET + self._active * PITCH - self.height() / 2
        self._scroll = max(0.0, min(self._overflow, target))
        self.update()

    # -- paint ---------------------------------------------------------
    def paintEvent(self, event) -> None:
        if not self._turns or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        right = float(self.width())
        for index in range(len(self._turns)):
            y = self._mark_y(index)
            if y < -MARK_H or y > self.height() + MARK_H:
                continue
            if index == self._active:
                self._mark(p, right, y, W_ACTIVE, ACTIVE)
            elif index == self._hover:
                self._mark(p, right, y, W_HOVER, HOVER)
            else:
                self._mark(p, right, y, W_IDLE, IDLE)
        if self._overflow > 0:
            self._fade_edges(p)
        p.end()

    @staticmethod
    def _mark(p: QPainter, right: float, y: float, width: int, pen: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pen)
        p.drawRoundedRect(QRectF(right - width, y - MARK_H / 2, width, MARK_H), 1.0, 1.0)

    def _fade_edges(self, p: QPainter) -> None:
        solid, clear = color(C.bg, 255), color(C.bg, 0)
        if self._scroll > 0.5:
            grad = QLinearGradient(0, 0, 0, FADE)
            grad.setColorAt(0.0, solid)
            grad.setColorAt(1.0, clear)
            p.fillRect(QRectF(0, 0, self.width(), FADE), grad)
        if self._scroll < self._overflow - 0.5:
            grad = QLinearGradient(0, self.height() - FADE, 0, self.height())
            grad.setColorAt(0.0, clear)
            grad.setColorAt(1.0, solid)
            p.fillRect(QRectF(0, self.height() - FADE, self.width(), FADE), grad)

    # -- pointer -------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        hit = self._index_at(event.position().y())
        if hit != self._hover:
            self._hover = hit
            self.update()
            self._show_card(hit)

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self._card.hide()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._index_at(event.position().y())
        if 0 <= hit < len(self._turns) and self._turns[hit].node_id:
            self.navigate.emit(self._turns[hit].node_id)

    def wheelEvent(self, event) -> None:
        if self._overflow <= 0:
            event.ignore()
            return
        step = event.angleDelta().y() / 120.0 * PITCH * 3
        self._scroll = max(0.0, min(self._overflow, self._scroll - step))
        self.update()
        if self._hover >= 0:
            self._show_card(self._hover)
        event.accept()

    def _show_card(self, index: int) -> None:
        if index < 0 or index >= len(self._turns):
            self._card.hide()
            return
        turn = self._turns[index]
        self._card.set_turn(index, turn.prompt, turn.response)
        height = self._card.height()
        top = self._mark_y(index) - height / 2
        top = max(-SHADOW, min(top, self.height() - height + SHADOW))
        self._card.move(
            int(self.x() - CARD_GAP - CARD_W - SHADOW),
            int(self.y() + top),
        )
        self._card.raise_()
        self._card.show()
