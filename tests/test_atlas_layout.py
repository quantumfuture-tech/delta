"""ATLAS: two axes, two rulers, overflow routing, panning and the minimap."""
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from delta.core import commands
from delta.core.model import Conversation
from delta.ui.atlas import MM_CHIP, MM_MIN, X_MAX, Y_MAX, ZOOM_STEP, AtlasView


def chain(layers: int) -> Conversation:
    c = Conversation(title="atlas")
    parent = None
    for index in range(layers):
        role = "user" if index % 2 == 0 else "assistant"
        text = f"第 {index + 1} 层"
        if parent is None:
            node = commands.add_root(c, text, model="m")
        else:
            node = commands.add_child(c, parent, role, text, model="m", status="complete")
            if index % 4 == 0:
                commands.add_child(c, parent, role, text + "（另一支）", model="m", status="complete")
        parent = node.id
    c.set_baseline(c.view()[0].id)
    return c


@pytest.fixture
def atlas(qapp):
    view = AtlasView()
    view.resize(340, 950)
    view.show()
    yield view
    view.close()
    view.deleteLater()


def open_atlas(view, layers=40) -> Conversation:
    view.set_conversation(chain(layers))
    QTest.qWait(40)
    return view.convo


def mouse(view, kind, x, y, button=Qt.MouseButton.LeftButton):
    pos = QPointF(x, y)
    event = QMouseEvent(
        kind,
        pos,
        QPointF(view.mapToGlobal(QPoint(int(x), int(y)))),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view, event)


def wheel(view, dy, *, ctrl=False, x=100, y=300):
    event = QWheelEvent(
        QPointF(x, y),
        QPointF(view.mapToGlobal(QPoint(x, y))),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view, event)


def test_fit_crams_and_fixed_keeps_the_pitch(atlas):
    open_atlas(atlas, 40)
    fitted = atlas._unit["y"]
    assert fitted < 24
    assert atlas.overflow().y() == pytest.approx(0, abs=0.5)
    assert not atlas.minimap_zone(QPointF(0, 0))

    atlas._axis["y"] = {"mode": "fixed", "value": 48.0}
    atlas.refresh()
    assert atlas._unit["y"] == pytest.approx(48.0)
    assert atlas.overflow().y() > 800


def test_drag_the_left_ruler_pins_the_axis(atlas):
    open_atlas(atlas, 40)
    fitted = atlas._unit["y"]
    y = atlas.plot_rect().center().y()
    mouse(atlas, QMouseEvent.Type.MouseButtonPress, 8, y)
    mouse(atlas, QMouseEvent.Type.MouseMove, 8, y + 80)
    mouse(atlas, QMouseEvent.Type.MouseButtonRelease, 8, y + 80)
    assert atlas._axis["y"]["mode"] == "fixed"
    assert atlas._axis["y"]["value"] > fitted


def test_double_click_returns_the_axis_to_fit(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 80.0}
    atlas.refresh()
    y = atlas.plot_rect().center().y()
    mouse(atlas, QMouseEvent.Type.MouseButtonDblClick, 8, y)
    assert atlas._axis["y"]["mode"] == "fit"


def test_zoom_scales_only_the_pinned_axis(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 48.0}
    atlas.refresh()
    wheel(atlas, 120, ctrl=True)
    assert atlas._axis["y"]["value"] == pytest.approx(48.0 * ZOOM_STEP)
    assert atlas._axis["x"]["mode"] == "fit"

    atlas._axis["x"] = {"mode": "fixed", "value": 40.0}
    atlas.refresh()
    wheel(atlas, 120, ctrl=True)
    assert atlas._axis["x"]["value"] == pytest.approx(40.0 * ZOOM_STEP)
    assert atlas._axis["x"]["value"] <= X_MAX
    assert atlas._axis["y"]["value"] <= Y_MAX


def test_zoom_does_nothing_when_both_axes_fit(atlas):
    open_atlas(atlas, 8)
    before = dict(atlas._axis["y"])
    wheel(atlas, 120, ctrl=True)
    assert atlas._axis["y"] == before


def test_wheel_goes_to_the_only_overflowing_axis(atlas):
    open_atlas(atlas, 40)
    # horizontal only: many leaf slots, vertical fitted
    atlas._axis["x"] = {"mode": "fixed", "value": 220.0}
    atlas._axis["y"] = {"mode": "fit", "value": 48.0}
    atlas.refresh()
    assert atlas.overflow().x() > 0
    assert atlas.overflow().y() == pytest.approx(0, abs=0.5)
    wheel(atlas, -240)
    assert atlas._scroll.x() > 0
    assert atlas._scroll.y() == 0


def test_wheel_is_vertical_when_both_overflow(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 60.0}
    atlas._axis["x"] = {"mode": "fixed", "value": 220.0}
    atlas.refresh()
    assert atlas.overflow().y() > 0 and atlas.overflow().x() > 0
    before_x = atlas._scroll.x()
    wheel(atlas, -240)
    assert atlas._scroll.y() > 0
    assert atlas._scroll.x() == before_x


def test_dragging_the_canvas_pans_instead_of_clicking(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 60.0}
    atlas.refresh()
    atlas._follow = True
    clicks: list[str] = []
    atlas.node_clicked.connect(clicks.append)
    plot = atlas.plot_rect()
    start = QPointF(plot.center().x(), plot.center().y())
    mouse(atlas, QMouseEvent.Type.MouseButtonPress, start.x(), start.y())
    mouse(atlas, QMouseEvent.Type.MouseMove, start.x(), start.y() - 40)
    mouse(atlas, QMouseEvent.Type.MouseButtonRelease, start.x(), start.y() - 40)
    assert atlas._scroll.y() > 0
    assert clicks == []
    assert atlas._follow is False


def test_minimap_moves_and_resizes_inside_the_canvas(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 60.0}
    atlas.refresh()
    box = atlas.minimap_rect()
    plot = atlas.plot_rect()
    assert plot.contains(box)

    # drag the anchor triangle far outside; the box clamps to the canvas
    ax = box.right() - 6
    ay = box.top() + 6
    mouse(atlas, QMouseEvent.Type.MouseButtonPress, ax, ay)
    mouse(atlas, QMouseEvent.Type.MouseMove, -200, -200)
    mouse(atlas, QMouseEvent.Type.MouseButtonRelease, -200, -200)
    moved = atlas.minimap_rect()
    assert plot.contains(moved)
    assert moved.left() >= plot.left()

    # drag the bottom-left corner to resize; the top-right stays put
    right_before = moved.right()
    mouse(atlas, QMouseEvent.Type.MouseButtonPress, moved.left() + 4, moved.bottom() - 4)
    mouse(atlas, QMouseEvent.Type.MouseMove, moved.left() + 4 + 30, moved.bottom() - 4 - 30)
    mouse(atlas, QMouseEvent.Type.MouseButtonRelease, moved.left() + 4 + 30, moved.bottom() - 4 - 30)
    resized = atlas.minimap_rect()
    assert resized.right() == pytest.approx(right_before, abs=1.0)
    assert resized.width() >= MM_MIN
    assert resized.height() >= MM_MIN


def test_clicking_the_minimap_pans_the_canvas(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 60.0}
    atlas.refresh()
    box = atlas.minimap_rect()
    atlas._scroll = QPointF(0, 0)
    mouse(atlas, QMouseEvent.Type.MouseButtonPress, box.center().x(), box.center().y())
    mouse(atlas, QMouseEvent.Type.MouseButtonRelease, box.center().x(), box.center().y())
    assert atlas._scroll.y() > 0


def test_minimap_collapses_to_a_chip_and_back(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 60.0}
    atlas.refresh()
    full = atlas.minimap_rect()
    assert full.width() >= MM_MIN
    assert atlas.minimap_zone(QPointF(full.left() + 4, full.top() + 4)) == "collapse"

    atlas.set_minimap_open(False)
    chip = atlas.minimap_rect()
    assert chip.width() == pytest.approx(MM_CHIP)
    assert chip.right() == pytest.approx(full.right(), abs=1)
    assert atlas.minimap_zone(chip.center()) == "chip"

    atlas.set_minimap_open(True)
    assert atlas.minimap_rect().width() == pytest.approx(full.width(), abs=1)


def test_the_hover_card_moves_out_of_the_way(atlas):
    open_atlas(atlas, 40)
    atlas._axis["y"] = {"mode": "fixed", "value": 48.0}
    atlas._follow = False
    atlas.refresh()
    plot = atlas.plot_rect()
    upper = min(atlas._pix.items(), key=lambda kv: kv[1].y())
    lower = max(atlas._pix.items(), key=lambda kv: kv[1].y())
    assert atlas.hover_box(upper[0]).top() > plot.center().y()
    assert atlas.hover_box(lower[0]).top() < plot.center().y()

