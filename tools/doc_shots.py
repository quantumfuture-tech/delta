"""Render the README screenshots from a deterministic conversation.

    python tools/doc_shots.py [outdir]

No network, no API key, no manual clicking: one tree with forks, a baseline in
the middle, ATLAS open and the turn rail on.  Writes into docs/screenshots:

    main.png    the whole window
    tabs.png    the tab strip around the watermark (grey above, gold below the rail)
    atlas.png   ATLAS at fixed pitch with the minimap
    rail.png    the turn rail with its preview card open
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-demo")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from delta.core import commands
from delta.core.model import Conversation
from delta.core.store import Store
from delta.ui import theme
from delta.ui.main_window import MainWindow
from tests.test_ui import pump

TURNS = [
    ("把默认孩子记成一个指针，比另存整条路径好在哪？",
     "路径存的是你走过的那条，指针存的是这一层你选了谁。前者会把还没发生的选择也冻住，跳一次就要重写沿途每一层。"),
    ("那跳到另一支的时候，祖先的选择会被改写吗？",
     "不会。基准之上走 parent，那是事实；之下走默认，那是选择。所以跳转只换后缀。"),
    ("分叉多了以后，标签行会不会退化成一排目录？",
     "会。所以每层只留一行标签行：长对话因此比上一轮更长，但不会多出一棵树。"),
    ("如果基准也可以是一个回答呢？",
     "可以。基准是一个节点，不区分提问与回答；它只说明「从这里往下才是我的选择」。"),
    ("那锁的边界画在哪？",
     "锁的是层，不是控件。捷径链经过的每一条边对应的层都被锁住，判定在调用时现算，从不单独存。"),
    ("层被锁着，还能开槽打字吗？",
     "能。槽不写层，只有发送那一刻才过门；撞锁就进入两步确认，草稿全程保留。"),
    ("确认抬基准之后，草稿还在吗？",
     "在。草稿挂在槽上，抬基准只改基准，不动任何一层。"),
    ("ATLAS 点一下算什么？",
     "算一次设基准。锁集不存，每次从捷径链派生——派生状态一旦存下来就会和事实打架。"),
    ("拖动标签的时候，页面滚动会跟着变吗？",
     "会。滚轮和贴边自动滚都经滚动条的 valueChanged 重算落点：指针不动、条带在动时，落点必须跟着动。"),
    ("那虚线到底表示什么？",
     "没选。虚线不是禁用，只是这一层现在不走它。线型只有一个语法：实线是默认，虚线是备选。"),
    ("为什么不用画布？",
     "画布把 95% 的阅读时间换成了分叉体验。这一轮要的是「一眼能分辨」，不是「可以拖来拖去」。"),
    ("捷径写进默认之后还能回头吗？",
     "能，回到默认路线就是把基准抬回根。写默认只写链上各层，不动基准，也不解锁。"),
]

SIDE = [
    ("反过来想：如果分叉完全免费，会带来什么新的负担？", "负担会转移到「选择」上：每次都要先决定要不要分叉。"),
    ("如果一屏只能放一条链，那 ATLAS 应该常驻吗？", "不该。它是地图不是正文，收起之后正文仍然成立。"),
]


def build() -> tuple[Conversation, str]:
    """The main chain plus two side branches; returns the baseline node id."""
    c = Conversation(title="分叉，作为一种思考方式")
    parent = None
    turns: list[tuple[str, str, str]] = []
    for index, (prompt, answer) in enumerate(TURNS):
        role = "user" if index % 2 == 0 else "assistant"
        node = (
            commands.add_root(c, prompt, model="deepseek-chat")
            if parent is None
            else commands.add_child(c, parent, "user", prompt, model="deepseek-chat")
        )
        answer_node = commands.add_child(
            c, node.id, "assistant", answer, model="deepseek-chat", status="complete"
        )
        commands.set_active(c, node.id, answer_node.id)
        turns.append((node.id, answer_node.id, prompt))
        parent = answer_node.id
        if index in (2, 5):
            side_prompt, side_answer = SIDE[0 if index == 2 else 1]
            side = commands.add_child(c, node.id, "user", side_prompt, model="deepseek-chat", activate=False)
            commands.add_child(
                c, side.id, "assistant", side_answer, model="deepseek-chat", status="complete", activate=False
            )

    # baseline on the answer of the fourth turn: turns above it are the locked
    # shortcut chain, the route continues below
    baseline = turns[3][1]
    c.set_baseline(baseline)
    return c, baseline


def crop(win: MainWindow, x: int, y: int, w: int, h: int, scale: float = 1.0):
    shot = win.grab().copy(x, y, w, h)
    if scale != 1.0:
        shot = shot.scaled(
            int(w * scale),
            int(h * scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return shot


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    outdir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply(app)

    store = Store(ROOT / "build" / "doc_data")
    convo, _ = build()
    store.save(convo)

    win = MainWindow(store, store.read_settings(), convo)
    win.resize(1500, 950)
    win.show()
    pump(app, 900)

    # ATLAS on its own grid: a fixed pitch and a fixed slot width, so the tree
    # keeps its shape instead of being squeezed into the panel
    win.atlas_panel.setFixedWidth(320)
    atlas = win.atlas
    atlas._axis = {
        "y": {"mode": "fixed", "value": 48.0},
        "x": {"mode": "fixed", "value": 46.0},
    }
    atlas._follow = False
    atlas._scroll.setY(140)
    atlas.refresh()
    pump(app, 300)

    transcript = win.transcript
    baseline_id = convo.baseline_id
    transcript.scroll_to_node(baseline_id, offset=220)
    pump(app, 300)
    win.grab().save(str(outdir / "main.png"))

    # tab strip around the watermark: a layer above (grey, locked) and the one below
    view = convo.view()
    ids = [node.id for node in view]
    index = ids.index(baseline_id)
    anchor = transcript.card_for(baseline_id)
    if anchor is not None:
        top_left = anchor.mapTo(win, QPoint(0, 0))
        crop(win, 150, max(96, top_left.y() - 150), 950, 320).save(str(outdir / "tabs.png"))

    # ATLAS panel: header, rulers, tree and minimap at 1.5x
    panel = win.atlas_panel
    origin = panel.mapTo(win, QPoint(0, 0))
    crop(win, origin.x(), origin.y(), panel.width(), 620, 1.5).save(str(outdir / "atlas.png"))

    # turn rail with a preview card: hover a mark in the route half
    rail = transcript.turnrail
    rail.place(transcript.viewport().width(), transcript.viewport().height(), 0)
    turns = transcript.rail_turns()
    target = next((i for i, t in enumerate(turns) if not t.locked), len(turns) - 1)
    if rail.isVisible():
        y = rail._mark_y(target)
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(rail.width() - 6, y),
            QPointF(rail.mapToGlobal(QPoint(rail.width() - 6, int(y)))),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(rail, event)
        pump(app, 200)
        rail_origin = rail.mapTo(win, QPoint(0, 0))
        left = max(0, rail_origin.x() - 370)
        crop(
            win,
            left,
            max(0, rail_origin.y() - 70),
            410,
            min(rail.height() + 200, 560),
            2.0,
        ).save(str(outdir / "rail.png"))

    win.close()
    win.deleteLater()
    pump(app, 60)
    print("ok", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
