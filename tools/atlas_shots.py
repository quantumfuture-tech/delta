"""Render ATLAS screenshots: one tree, two baselines (root and middle).

    python tools/atlas_shots.py [outdir]

Writes atlas_baseline_root.png / atlas_baseline_mid.png (1280x820 window) and
the matching *_atlas2x.png crops.  Deterministic demo tree, no API calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-demo")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from delta.core import commands
from delta.core.model import Conversation
from delta.core.store import Store
from delta.ui import theme
from delta.ui.main_window import MainWindow

U = "user"
A = "assistant"

TEXT = {
    "u1": "把默认孩子记成一个指针，比另存整条路径好在哪？",
    "a1": "路径存的是“你走过的那条”，指针存的是“这一层你选了谁”。前者会把还没发生的选择也冻住，跳一次就要重写沿途每一层。",
    "u2": "那跳到另一支的时候，祖先的选择会被改写吗？",
    "u3": "分叉多了以后，标签行会不会退化成一排目录？",
    "u4": "如果基准也可以是一个回答呢？",
    "a2": "不会。基准之上走 parent，那是事实；之下走默认，那是选择。",
    "a3": "只换后缀，前缀控件原样保留，切回来复用同一张卡片。",
    "u5": "那锁的边界画在哪？",
    "u8": "把捷径写进默认之后，还能回到默认路线吗？",
    "a6": "锁的是层，不是控件。捷径链经过的每一条边对应的层都被锁住，判定在调用时现算。",
    "u6": "层被锁着，还能开槽打字吗？",
    "a7": "能。槽不写层，只有发送那一刻才过门；撞锁就进入两步确认，草稿全程保留。",
    "u7": "确认抬基准之后，草稿还在吗？",
    "a8": "在。草稿挂在槽上，抬基准只改基准，不动任何一层。",
    "a4": "会。所以每层只留一行 48px 的紧凑标签；长对话因此比上一轮更长。",
    "a5": "可以。基准是一个节点，不区分提问与回答。",
    "u9": "那 ATLAS 点一下算什么？",
    "u10": "锁集要不要单独存一份？",
    "a11": "算一次设基准。锁集不存，它每次从捷径链派生。",
}


def build() -> Conversation:
    c = Conversation(title="分叉，作为一种思考方式")

    def kid(parent: str, role: str, key: str, *, activate: bool = True) -> str:
        return commands.add_child(c, parent, role, TEXT[key], activate=activate).id

    u1 = commands.add_root(c, TEXT["u1"], model="deepseek-chat").id
    a1 = kid(u1, A, "a1")
    u2 = kid(a1, U, "u2")
    u3 = kid(a1, U, "u3", activate=False)
    u4 = kid(a1, U, "u4", activate=False)
    kid(u3, A, "a4")
    a5 = kid(u4, A, "a5")
    u9 = kid(a5, U, "u9")
    kid(a5, U, "u10", activate=False)
    kid(u9, A, "a11")
    a2 = kid(u2, A, "a2")
    kid(u2, A, "a3", activate=False)
    u5 = kid(a2, U, "u5")
    kid(a2, U, "u8", activate=False)
    a6 = kid(u5, A, "a6")
    u6 = kid(a6, U, "u6")
    a7 = kid(u6, A, "a7")
    u7 = kid(a7, U, "u7")
    kid(u7, A, "a8")
    commands.set_active(c, a1, u2)
    c.set_baseline(u1)
    return c


def shot(outdir: Path, name: str, where: str) -> Path:
    store = Store(outdir / "data")
    convo = build()
    if where == "mid":
        convo.set_baseline(next(n.id for n in convo.nodes.values() if n.text == TEXT["a2"]))
    store.save(convo)

    win = MainWindow(store, store.read_settings(), convo)
    win.resize(1280, 820)
    win.show()
    QTest.qWait(300)
    QApplication.processEvents()

    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{name}.png"
    win.grab().save(str(path))

    panel = win.atlas_panel
    top = panel.mapTo(win, panel.rect().topLeft())
    cut = win.grab(panel.rect().translated(top))
    cut = cut.scaled(
        cut.width() * 2,
        cut.height() * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    cut.save(str(outdir / f"{name}_atlas2x.png"))

    win.close()
    win.deleteLater()
    QApplication.processEvents()
    return path


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "atlas"
    app = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply(app)
    shot(outdir, "atlas_baseline_root", "root")
    shot(outdir, "atlas_baseline_mid", "mid")
    print("ok", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
