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