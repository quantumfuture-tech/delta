"""The turn rail: grouping, the reading line, the click, and the overflow cap."""
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from delta.core import commands
from delta.core.model import Conversation
from delta.ui.transcript import TranscriptView
from delta.ui.turnrail import CARD_GAP, MAX_H, PITCH, SHADOW

LONG = "这一段回答要足够长，才能把每一轮撑到一百多像素高，让读线测试不会被两行字骗过去。" * 3


def chain(turns: int) -> Conversation:
    c = Conversation(title="rail")
    parent = None
    for index in range(turns):
        prompt = f"第 {index + 1} 个问题，问的是一件够长的事。"
        answer = f"{index + 1}: {LONG}"
        if parent is None:
            u = commands.add_root(c, prompt, model="m")
        else:
            u = commands.add_child(c, parent, "user", prompt, model="m")
        a = commands.add_child(c, u.id, "assistant", answer, model="m", status="complete")
        commands.set_active(c, u.id, a.id)
        parent = a.id
    c.set_baseline(c.view()[0].id)
    return c


@pytest.fixture
def view(qapp):
    v = TranscriptView()
    v.resize(1300, 820)
    v.show()
    yield v
    v.close()
    v.deleteLater()


def open_view(view, turns: int) -> Conversation:
    c = chain(turns)
    view.set_conversation(c)
    QTest.qWait(60)
    view.verticalScrollBar().setValue(0)
    QTest.qWait(30)
    return c


def test_turns_group_prompt_with_its_answer(view):
    c = open_view(view, 4)
    turns = view.rail_turns()
    assert [t.prompt[:2] for t in turns] == ["第 ", "第 ", "第 ", "第 "]
    assert turns[0].node_id == c.view()[0].id
    assert turns[1].response.startswith("2:")
    assert len(turns) == 4


def test_rail_active_is_the_last_turn_at_the_bottom(view):
    open_view(view, 10)
    view.scroll_to_bottom()
    QTest.qWait(60)
    turns = view.rail_turns()
    assert view.rail_active(turns) == len(turns) - 1


def test_rail_active_follows_the_reading_line(view):
    c = open_view(view, 10)
    target = c.view()[11].id  # user node of the sixth turn
    view.scroll_to_node(target, offset=24)
    QTest.qWait(40)
    assert view.rail_active(view.rail_turns()) == 5
    view.verticalScrollBar().setValue(0)
    QTest.qWait(40)
    assert view.rail_active(view.rail_turns()) == 0


def test_click_scrolls_to_the_turn_and_leaves_the_baseline_alone(view):
    c = open_view(view, 10)
    baseline = c.baseline_id
    view.scroll_to_bottom()
    QTest.qWait(60)
    rail = view.turnrail
    turns = view.rail_turns()
    assert rail.isVisible()

    index = 2
    y = rail._mark_y(index)
    for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(
            kind,
            QPointF(rail.width() - 6, y),
            QPointF(rail.mapToGlobal(QPoint(rail.width() - 6, int(y)))),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(rail, event)
    QTest.qWait(60)

    assert c.baseline_id == baseline
    bar = view.verticalScrollBar()
    card = view.card_for(turns[index].node_id)
    top = card.mapTo(view.viewport(), QPoint(0, 0)).y()
    assert 18 <= top <= 30
    assert bar.value() > 0


def test_rail_is_hidden_for_a_single_turn(view):
    open_view(view, 1)
    assert view.rail_turns() and len(view.rail_turns()) == 1
    assert not view.turnrail.isVisible()


def test_long_session_caps_the_frame_and_scrolls_inside_it(view):
    open_view(view, 60)
    rail = view.turnrail
    assert rail.isVisible()
    assert rail.height() == MAX_H
    assert rail._overflow == pytest.approx((60 - 1) * PITCH + 12 - MAX_H)
    first = rail._mark_y(0)
    assert rail._mark_y(1) - first == pytest.approx(PITCH)

    event = QWheelEvent(
        QPointF(rail.width() - 6, 40),
        QPointF(rail.mapToGlobal(QPoint(rail.width() - 6, 40))),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(rail, event)
    assert rail._scroll > 0
    assert rail._scroll <= rail._overflow


def test_short_rail_ignores_the_wheel(view):
    open_view(view, 6)
    rail = view.turnrail
    rail._scroll = 0.0
    event = QWheelEvent(
        QPointF(rail.width() - 6, 20),
        QPointF(rail.mapToGlobal(QPoint(rail.width() - 6, 20))),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(rail, event)
    assert rail._scroll == 0.0


def test_preview_card_follows_the_pointer(view):
    open_view(view, 20)  # tall enough that the card is not clamped by the frame
    rail = view.turnrail
    QTest.qWait(30)
    index = 10
    y = rail._mark_y(index)
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(rail.width() - 6, y),
        QPointF(rail.mapToGlobal(QPoint(rail.width() - 6, int(y)))),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(rail, event)
    QTest.qWait(30)
    assert rail._hover == index
    assert rail._card.isVisible()
    card = rail._card
    # the card sits left of the rail, its middle on the hovered mark; the widget
    # carries an even SHADOW margin, so the surface is the part that must clear
    assert card.x() + card.width() - SHADOW == pytest.approx(rail.x() - CARD_GAP, abs=1)
    assert abs((card.y() + card.height() / 2) - (rail.y() + y)) <= 2
