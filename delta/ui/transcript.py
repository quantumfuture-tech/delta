"""The reading surface: a single spine, with only the affected suffix replaced.

The widget tree is keyed by node/fork id.  Switching a branch finds the longest
common prefix and only removes/creates the rows after the fork; streaming
updates exactly one card.  The scroll anchor is a (row key, offset) pair, not a
raw pixel value, so a restart with a different window size still comes back to
the same words.
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ..core.model import Conversation
from .forkbar import LayerBar
from .message import MessageCard
from .theme import C, FONT_MONO, color, font
from .turnrail import Turn, TurnRail


class WaterLine(QWidget):
    """The lock waterline: everything above it is the pinned shortcut chain."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = self.height() - 6
        p.setPen(QPen(color(C.brass_dim, 170), 1))
        p.drawLine(QPointF(21, y), QPointF(self.width() - 10, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(C.bg))
        p.drawEllipse(QRectF(17.5, y - 3.5, 7, 7))
        p.setPen(QPen(color(C.brass_dim, 220), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(17.5, y - 3.5, 7, 7))
        p.setFont(font(FONT_MONO, 7.0, 500, 0.5))
        p.setPen(color(C.brass_dim))
        p.drawText(
            QRectF(self.width() - 340, y - 17, 330, 13),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "基准 · 以上锁止，以下自由",
        )
        p.end()


class TranscriptView(QScrollArea):
    activate_requested = Signal(str, str)
    tail_slot_requested = Signal(str)
    slot_requested = Signal(str, str, str)
    close_requested = Signal(str, str)
    reorder_requested = Signal(str, str, int)
    baseline_requested = Signal(str)
    menu_requested = Signal(str, QPoint)
    typing_started = Signal()
    notice = Signal(str, str)
    view_moved = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.convo: Conversation | None = None
        self._rows: list[tuple[str, QWidget]] = []
        self._cards: dict[str, MessageCard] = {}
        self._pinned_ids: set[str] = set()
        self._layers: dict[str, LayerBar] = {}
        self._open_parent: str | None = None
        self._open_base: str | None = None
        self._cache: "OrderedDict[str, QWidget]" = OrderedDict()
        self._cache_host = QWidget(self)
        self._cache_host.hide()
        self._internal = False
        self._at_bottom = True
        self._pending_restore: dict | None = None
        self._restore_tries = 0
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(140)
        self._save_timer.timeout.connect(self._emit_view)
        self._bottom_timer = QTimer(self)
        self._bottom_timer.setSingleShot(True)
        self._bottom_timer.setInterval(0)
        self._bottom_timer.timeout.connect(self._pin_bottom)

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QScrollArea { background: transparent; border:none; }")
        self.viewport().setAutoFillBackground(False)

        self._content = QWidget()
        self._content.setObjectName("FeedContent")
        self._content.setStyleSheet("QWidget#FeedContent { background: transparent; }")
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        h = QHBoxLayout(self._content)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addStretch(1)
        self._column = QWidget(self._content)
        self._column.setMaximumWidth(890)
        self._column.setMinimumWidth(420)
        self._column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._feed = QVBoxLayout(self._column)
        self._feed.setContentsMargins(0, 16, 0, 56)
        self._feed.setSpacing(0)
        self._feed.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._feed.addStretch(1)
        h.addWidget(self._column, 10)
        h.addStretch(1)
        self.setWidget(self._content)
        self._fade = _BottomFade(self.viewport())
        self._fade.raise_()
        self.turnrail = TurnRail(self.viewport())
        self.turnrail.navigate.connect(self._rail_navigate)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.verticalScrollBar().rangeChanged.connect(self._on_range)

    # -- model wiring --------------------------------------------------
    def set_conversation(self, convo: Conversation, *, restore: dict | None = None) -> None:
        self.convo = convo
        self._clear_rows()
        self._cards.clear()
        self._layers.clear()
        self._open_parent = None
        self._open_base = None
        self._pending_restore = restore
        self._restore_tries = 0
        self._at_bottom = bool(restore.get("at_bottom", False)) if restore else True
        self._bottom_timer.stop()
        self.rebuild(restore=restore)

    def _clear_rows(self) -> None:
        for _, w in self._rows:
            w.setParent(None)
            w.deleteLater()
        self._rows = []
        for _, w in self._cache.items():
            w.setParent(None)
            w.deleteLater()
        self._cache.clear()

    def _stash(self, key: str, widget: QWidget) -> None:
        node_id = key[2:]
        if node_id not in (self.convo.nodes if self.convo else {}):
            widget.setParent(None)
            widget.deleteLater()
            return
        widget.hide()
        widget.setParent(self._cache_host)
        self._cache[key] = widget
        self._cache.move_to_end(key)
        if len(self._cache) > 80:
            _, old = self._cache.popitem(last=False)
            old.setParent(None)
            old.deleteLater()

    def _reuse(self, key: str) -> QWidget | None:
        widget = self._cache.pop(key, None)
        if widget is not None:
            widget.setParent(self._column)
            return widget
        return None

    def _spec(self) -> list[tuple[str, tuple]]:
        c = self.convo
        if c is None:
            return []
        chain = c.view()
        rows: list[tuple[str, tuple]] = []
        last = len(chain) - 1
        for i, node in enumerate(chain):
            rows.append(("n:" + node.id, ("node", node, i == last)))
            if i < last and node.id == c.baseline_id:
                rows.append(("w:" + node.id, ("waterline", node.id)))
            active = chain[i + 1].id if i < last else None
            rows.append(("l:" + node.id, ("layer", node.id, active)))
        return rows

    def rebuild(self, *, restore: dict | None = None, keep_bottom: bool | None = None) -> None:
        if self.convo is None:
            return
        if restore is not None:
            self._at_bottom = bool(restore.get("at_bottom", False))
            self._bottom_timer.stop()
        sticky = self._at_bottom if keep_bottom is None else keep_bottom
        self._pinned_ids = {n.id for n in self.convo.shortcut_chain()}
        spec = self._spec()
        old_keys = [k for k, _ in self._rows]
        new_keys = [k for k, _ in spec]
        common = 0
        while common < len(old_keys) and common < len(new_keys) and old_keys[common] == new_keys[common]:
            common += 1

        for key, w in self._rows[common:]:
            self._stash(key, w)
        self._rows = self._rows[:common]

        for idx in range(common, len(spec)):
            key, desc = spec[idx]
            w = self._make_widget(key, desc)
            self._rows.append((key, w))
            self._feed.insertWidget(idx, w)
            w.show()

        self._refresh_existing(spec[:common])
        stale = [k for k, w in self._cache.items() if k[2:] not in self.convo.nodes]
        for k in stale:
            w = self._cache.pop(k)
            w.setParent(None)
            w.deleteLater()
        self._cards = {k[2:]: w for k, w in self._rows if k.startswith("n:") and isinstance(w, MessageCard)}
        self._layers = {k[2:]: w for k, w in self._rows if k.startswith("l:") and isinstance(w, LayerBar)}
        QTimer.singleShot(0, self._sync_mouths)

        if restore is not None:
            self._pending_restore = restore
            self._schedule_restore()
        elif sticky:
            self._bottom_timer.start()
        self._content.updateGeometry()
        self._column.updateGeometry()
        self._sync_rail()

    def _sync_mouths(self) -> None:
        """Cut each card's top edge where its layer's active tab sits on it."""
        if self.convo is None:
            return
        for node_id, card in self._cards.items():
            node = self.convo.nodes.get(node_id)
            bar = self._layers.get(node.parent_id) if node is not None and node.parent_id else None
            chip = None
            if bar is not None:
                chip = next((c for c in bar._chips if c.active and c.child.id == node_id), None)
            if chip is None:
                card.set_mouth(None)
                continue
            # the bar and the card share the column's left edge, and both have
            # the same 44px gutter, so the chip's x is already the panel's x
            scroll = bar._scroll.horizontalScrollBar().value()
            x0 = float(chip.x() - scroll)
            card.set_mouth((x0, x0 + chip.width()))

    def _make_widget(self, key: str, desc: tuple) -> QWidget:
        cached = self._reuse(key)
        if desc[0] == "waterline":
            if isinstance(cached, WaterLine):
                return cached
            if cached is not None:
                cached.setParent(None)
                cached.deleteLater()
            return WaterLine(self._column)
        if desc[0] == "node":
            _, node, current = desc
            if isinstance(cached, MessageCard) and cached.node is node:
                cached.refresh()
                cached.set_current(current)
                cached.set_pinned(node.id in self._pinned_ids)
                return cached
            if cached is not None:
                cached.setParent(None)
                cached.deleteLater()
            card = MessageCard(node, current=current)
            card.set_pinned(node.id in self._pinned_ids)
            card.copy_requested.connect(lambda _nid: self.notice.emit("已复制这条消息", "ok"))
            card.menu_requested.connect(self.menu_requested)
            card.body.typing_started.connect(self.typing_started)
            return card
        _, parent_id, active = desc
        open_base = self._open_base if self._open_parent == parent_id else None
        open_tail = self._open_parent == parent_id and self._open_base is None
        if isinstance(cached, LayerBar):
            cached.refresh(active)
            cached.set_open(open_base, open_tail)
            return cached
        if cached is not None:
            cached.setParent(None)
            cached.deleteLater()
        bar = LayerBar(
            self.convo,
            parent_id,
            active_child=active,
            open_base=open_base,
            open_tail=open_tail,
        )
        bar._scroll.horizontalScrollBar().valueChanged.connect(self._sync_mouths)
        bar.activate_requested.connect(self.activate_requested)
        bar.tail_slot_requested.connect(self.tail_slot_requested)
        bar.slot_requested.connect(self.slot_requested)
        bar.close_requested.connect(self.close_requested)
        bar.reorder_requested.connect(self.reorder_requested)
        bar.baseline_requested.connect(self.baseline_requested)
        return bar

    def set_open_slot(self, parent_id: str | None, base_id: str | None) -> None:
        """Mark the tab (or the tail chip) that the input is currently bound to."""
        self._open_parent = parent_id
        self._open_base = base_id
        for bar in self._layers.values():
            if bar.parent_id == parent_id:
                bar.set_open(base_id, base_id is None)
            else:
                bar.set_open(None, False)

    def _refresh_existing(self, spec_prefix: list[tuple]) -> None:
        if self.convo is None:
            return
        leaf = self.convo.view_leaf()
        ids = [n.id for n in self.convo.view()]
        for key, _ in spec_prefix:
            w = dict(self._rows).get(key)
            if w is None:
                continue
            if key.startswith("n:") and isinstance(w, MessageCard):
                w.set_current(key[2:] == leaf)
            elif key.startswith("l:") and isinstance(w, LayerBar):
                parent_id = key[2:]
                idx = ids.index(parent_id) if parent_id in ids else -1
                active = ids[idx + 1] if 0 <= idx < len(ids) - 1 else None
                w.refresh(active)

    # -- live updates --------------------------------------------------
    def update_stream(self, node_id: str) -> None:
        card = self._cards.get(node_id)
        if card is not None:
            card.update_stream()
            if self._at_bottom:
                self._bottom_timer.start()
            self._sync_rail()

    def refresh_node(self, node_id: str) -> None:
        card = self._cards.get(node_id)
        if card is not None:
            card.refresh()
            card.set_current(self.convo.view_leaf() == node_id if self.convo else False)
            if self._at_bottom:
                self._bottom_timer.start()
            self._sync_rail()

    def refresh_layer(self, parent_id: str, *, text_only: bool = False) -> None:
        bar = self._layers.get(parent_id)
        if bar is None or self.convo is None:
            return
        if text_only:
            bar.refresh_texts()
            return
        ids = [n.id for n in self.convo.view()]
        idx = ids.index(parent_id) if parent_id in ids else -1
        active = ids[idx + 1] if 0 <= idx < len(ids) - 1 else None
        bar.refresh(active)
        QTimer.singleShot(0, self._sync_mouths)

    def row_widgets(self) -> dict[str, QWidget]:
        return dict(self._rows)

    def card_for(self, node_id: str) -> MessageCard | None:
        return self._cards.get(node_id)

    def _focus_composer(self) -> None:
        win = self.window()
        if hasattr(win, "composer"):
            win.composer.focus_input()

    # -- turn rail -----------------------------------------------------
    def rail_turns(self) -> list[Turn]:
        """One entry per turn of the current view: a prompt and its answer.

        A turn starts at a user node.  A chain can also open on an answer (a
        baseline sits on an assistant node), so an orphan answer keeps its own
        place instead of disappearing from the ladder.
        """
        if self.convo is None:
            return []
        turns: list[Turn] = []
        for node in self.convo.view():
            if node.role == "user":
                turns.append(Turn(node.id, node.text or "", ""))
            elif not turns:
                turns.append(Turn(node.id, "", node.text or ""))
            else:
                last = turns[-1]
                turns[-1] = Turn(last.node_id, last.prompt, node.text or "")
        return turns

    def rail_active(self, turns: list[Turn]) -> int:
        """Which turn the reader is in: the one crossing the reading line.

        The line sits a little below the viewport top (at most 96px, or a fifth
        of the band), so a turn that has only just started still counts as the
        one being read.  At the bottom the last turn wins outright — otherwise a
        two-line final answer would never light up.
        """
        if not turns:
            return -1
        bar = self.verticalScrollBar()
        if bar.maximum() - bar.value() <= 2:
            return len(turns) - 1
        line = bar.value() + min(96, int(self.viewport().height() * 0.2))
        active = 0
        for index, turn in enumerate(turns):
            card = self._cards.get(turn.node_id)
            if card is None:
                continue
            if card.mapTo(self._content, QPoint(0, 0)).y() <= line:
                active = index
            else:
                break
        return active

    def _sync_rail(self) -> None:
        turns = self.rail_turns()
        self.turnrail.set_state(turns, self.rail_active(turns))
        vp = self.viewport()
        right = self._column.mapTo(vp, QPoint(self._column.width(), 0)).x()
        self.turnrail.place(vp.width(), vp.height(), right)

    def _rail_navigate(self, node_id: str) -> None:
        self.scroll_to_node(node_id, offset=24)
        # rows still settle after a rebuild (a card's height is only final once
        # it has been laid out), so land twice: now, then once the scroll range
        # has caught up
        QTimer.singleShot(30, lambda: self._land_on_turn(node_id))

    def _land_on_turn(self, node_id: str) -> None:
        card = self._cards.get(node_id)
        if card is not None:
            top = card.mapTo(self.viewport(), QPoint(0, 0)).y()
            if abs(top - 24) > 2:
                self.scroll_to_node(node_id, offset=24)
        self._sync_rail()

    # -- scroll --------------------------------------------------------
    def _on_range(self, min_v: int, max_v: int) -> None:
        if self._pending_restore is not None:
            self._schedule_restore()
            return
        if self._at_bottom and not self._internal:
            self._bottom_timer.start()

    def _on_scroll(self, value: int) -> None:
        if self._internal:
            return
        sb = self.verticalScrollBar()
        self._at_bottom = value >= sb.maximum() - 2
        self._save_timer.start()
        self._sync_rail()

    def _pin_bottom(self) -> None:
        if not self._at_bottom or self.convo is None:
            return
        sb = self.verticalScrollBar()
        self._internal = True
        try:
            sb.setValue(sb.maximum())
        finally:
            self._internal = False
        self._save_timer.start()
        self._sync_rail()

    def scroll_to_bottom(self) -> None:
        self._at_bottom = True
        self._bottom_timer.start()

    def _top_row(self) -> tuple[str, int] | None:
        y = self.verticalScrollBar().value()
        for key, w in self._rows:
            top = w.mapTo(self._content, QPoint(0, 0)).y()
            h = w.height()
            if top + h > y + 1:
                return key, top - y
        return None

    def capture(self) -> dict:
        top = self._top_row()
        sb = self.verticalScrollBar()
        return {
            "key": top[0] if top else None,
            "offset": top[1] if top else 0,
            "value": sb.value(),
            "at_bottom": self._at_bottom,
        }

    def capture_key(self, key: str) -> dict:
        y = self.verticalScrollBar().value()
        for k, w in self._rows:
            if k == key:
                top = w.mapTo(self._content, QPoint(0, 0)).y()
                return {"key": key, "offset": top - y, "value": y, "at_bottom": False}
        return self.capture()

    def restore(self, state: dict | None) -> None:
        self._pending_restore = state
        self._restore_tries = 0
        self._bottom_timer.stop()
        self._at_bottom = bool(state.get("at_bottom", False)) if state else True
        self._schedule_restore()

    def _schedule_restore(self) -> None:
        QTimer.singleShot(0, self._apply_restore)

    def _apply_restore(self) -> None:
        state = self._pending_restore
        if state is None:
            return
        self._restore_tries += 1
        sb = self.verticalScrollBar()
        target: int | None = None
        if state.get("at_bottom"):
            target = sb.maximum()
        else:
            key = state.get("key")
            if key:
                for k, w in self._rows:
                    if k == key:
                        top = w.mapTo(self._content, QPoint(0, 0)).y()
                        target = top - int(state.get("offset", 0))
                        break
            if target is None:
                target = int(state.get("value", 0))
        target = max(sb.minimum(), min(sb.maximum(), target))
        self._internal = True
        try:
            sb.setValue(target)
        finally:
            self._internal = False
        current = sb.value()
        if abs(current - target) > 1 and self._restore_tries < 6:
            QTimer.singleShot(16, self._apply_restore)
            return
        self._pending_restore = None
        self._at_bottom = current >= sb.maximum() - 2
        self._save_timer.start()
        self._sync_rail()

    def scroll_to_key(self, key: str, *, offset: int = 72) -> None:
        for k, w in self._rows:
            if k == key:
                top = w.mapTo(self._content, QPoint(0, 0)).y()
                sb = self.verticalScrollBar()
                target = max(0, min(sb.maximum(), top - offset))
                self._internal = True
                try:
                    sb.setValue(target)
                finally:
                    self._internal = False
                self._at_bottom = target >= sb.maximum() - 2
                self._save_timer.start()
                self._sync_rail()
                return

    def scroll_to_node(self, node_id: str, *, offset: int = 72) -> None:
        self.scroll_to_key("n:" + node_id, offset=offset)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_mouths()
        self._place_fade()
        self._sync_rail()
        if self._pending_restore is None:
            self._pending_restore = self.capture()
            self._restore_tries = 0
        QTimer.singleShot(30, self._apply_restore)

    def _place_fade(self) -> None:
        vp = self.viewport()
        self._fade.setGeometry(0, vp.height() - 38, vp.width(), 38)
        self._fade.raise_()

    def _emit_view(self) -> None:
        self.view_moved.emit(self.capture())

class _BottomFade(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, color(C.bg, 0))
        grad.setColorAt(1.0, color(C.bg, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(self.rect())
        p.end()