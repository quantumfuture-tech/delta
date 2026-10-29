"""Application entry point."""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from .core.model import Conversation
from .core.store import Store
from .ui import theme
from .ui.icons import draw_logo
from .ui.main_window import MainWindow
from .ui.theme import C, color


def default_data_dir() -> Path:
    env = os.environ.get("DELTA_DATA")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Delta"
    return Path.home() / ".delta"


def make_icon() -> QIcon:
    pm = QPixmap(128, 128)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color(C.bg_soft))
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 14, 14)
    draw_logo(p, QRectF(13, 15, 38, 38), color(C.brass), False)
    p.end()
    return QIcon(pm)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delta", description="Delta — a branching conversation client")
    parser.add_argument("--data", default=None, help="data directory (default: %%APPDATA%%/Delta)")
    parser.add_argument("--conversation", default=None, help="conversation id to open")
    return parser


def _install_trace(app: QApplication) -> None:
    """Record every window shown, when `DELTA_UI_TRACE` is set.

    A report of small empty windows at the screen corner after a tab drag could
    not be reproduced in-process; this gives the next report evidence instead of
    a guess.  See `delta/ui/trace.py`.
    """
    from .ui import trace

    if not trace.enabled():
        return

    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget

    class _Watch(QObject):
        def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt name)
            if event.type() in (QEvent.Type.Show, QEvent.Type.Hide) and isinstance(obj, QWidget) and obj.isWindow():
                what = "show" if event.type() == QEvent.Type.Show else "hide"
                parent = obj.parent()
                trace.write(
                    f"{what} {obj.__class__.__name__} {obj.geometry().getRect()} "
                    f"visible={obj.isVisible()} parent={parent.__class__.__name__ if parent else None} "
                    f"name={obj.objectName()!r}"
                )
            return False

    watcher = _Watch()
    app._ui_trace = watcher  # keep the filter alive for the app's lifetime
    app.installEventFilter(watcher)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Delta.Thinking.1")
        except Exception:
            pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Delta")
    app.setApplicationDisplayName("Delta")
    app.setOrganizationName("Delta")
    app.setWindowIcon(make_icon())
    theme.apply(app)
    _install_trace(app)

    data_dir = Path(args.data) if args.data else default_data_dir()
    store = Store(data_dir)
    settings = store.read_settings()
    convo: Conversation | None = None
    wanted = args.conversation or settings.get("last_id")
    if wanted:
        try:
            convo = store.load(str(wanted))
        except Exception:
            convo = None
    if convo is None:
        items = store.list()
        if items and not items[0].get("corrupt"):
            try:
                convo = store.load(str(items[0]["id"]))
            except Exception:
                convo = None
    if convo is None:
        convo = Conversation()
        try:
            store.save(convo)
        except Exception:
            pass

    window = MainWindow(store, settings, convo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())