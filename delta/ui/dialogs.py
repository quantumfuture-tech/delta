"""Frameless sheets: settings/about and destructive confirmation."""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import IconButton, PillButton


SHEET_QSS = f"""
QWidget#SheetPanel {{
    background: {C.panel};
    border: 1px solid {C.line_hi};
    border-radius: 16px;
}}
QLabel#SheetTitle {{ color: {C.ink}; background: transparent; }}
QLabel#SheetEyebrow {{ color: {C.brass_dim}; background: transparent; }}
QLabel#SheetMuted {{ color: {C.muted}; background: transparent; }}
QLabel#SheetText {{ color: {C.ink_dim}; background: transparent; }}
QLabel#SheetKey {{ color: {C.ink_soft}; background: transparent; font-family: 'Cascadia Code'; }}
"""


class SheetBase(QDialog):
    def __init__(self, parent, width: int, height: int) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedSize(width + 48, height + 48)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        self.panel = QWidget(self)
        self.panel.setObjectName("SheetPanel")
        self.panel.setStyleSheet(SHEET_QSS)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(42)
        shadow.setOffset(0, 10)
        shadow.setColor(color("#000000", 190))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)
        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(26, 22, 26, 20)
        self.body.setSpacing(10)
        self._drag = None
        if parent is not None:
            self.move(parent.frameGeometry().center() - self.rect().center())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def add_header(self, eyebrow: str, title: str) -> None:
        eb = QLabel(eyebrow, self.panel)
        eb.setObjectName("SheetEyebrow")
        eb.setFont(font(FONT_MONO, 8.0, 500, 1.2))
        self.body.addWidget(eb)
        t = QLabel(title, self.panel)
        t.setObjectName("SheetTitle")
        t.setFont(font(FONT_UI, 18, 600))
        self.body.addWidget(t)

    def add_close(self) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        close = PillButton("关闭", self.panel)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.body.addLayout(row)


class InfoSheet(SheetBase):
    def __init__(self, parent, *, data_dir: Path, key_present: bool, key_len: int, model: str, reload_cb) -> None:
        super().__init__(parent, 620, 500)
        self._reload_cb = reload_cb
        self._data_dir = data_dir
        self.body.setSpacing(8)
        self.add_header("DELTA / CONNECTION", "连接与设置")

        panel = QWidget(self.panel)
        panel.setStyleSheet(
            f"QWidget {{ background:{C.panel_hi}; border:1px solid {C.line}; border-radius:10px; }}"
            f"QLabel {{ border:none; background:transparent; }}"
        )
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 12, 16, 12)
        pl.setSpacing(6)
        self._key_label = QLabel(self._key_text(key_present, key_len), panel)
        self._key_label.setObjectName("SheetKey")
        self._key_label.setFont(font(FONT_MONO, 9.0))
        self._key_label.setStyleSheet(f"color:{C.sage if key_present else C.danger}; background:transparent;")
        endpoint = QLabel("endpoint   https://api.deepseek.com", panel)
        endpoint.setFont(font(FONT_MONO, 8.6))
        endpoint.setStyleSheet(f"color:{C.muted}; background:transparent;")
        model_line = QLabel(f"model      {model}", panel)
        model_line.setFont(font(FONT_MONO, 8.6))
        model_line.setStyleSheet(f"color:{C.muted}; background:transparent;")
        path_line = QLabel(f"data       {data_dir}", panel)
        path_line.setFont(font(FONT_MONO, 8.0))
        path_line.setStyleSheet(f"color:{C.faint}; background:transparent;")
        pl.addWidget(self._key_label)
        pl.addWidget(endpoint)
        pl.addWidget(model_line)
        pl.addWidget(path_line)
        self.body.addWidget(panel)

        key_note = QLabel("修改环境变量后，点下面的按钮即可在当前窗口重读，无需重启。", self.panel)
        key_note.setObjectName("SheetMuted")
        key_note.setFont(font(FONT_UI, 8.8))
        key_note.setWordWrap(True)
        self.body.addWidget(key_note)
        row = QHBoxLayout()
        reload_btn = PillButton("重新读取环境变量", self.panel)
        reload_btn.clicked.connect(self._reload)
        open_btn = PillButton("打开数据目录", self.panel)
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir))))
        row.addWidget(reload_btn)
        row.addWidget(open_btn)
        row.addStretch(1)
        self.body.addLayout(row)

        self.body.addSpacing(8)
        keys_title = QLabel("键盘", self.panel)
        keys_title.setObjectName("SheetEyebrow")
        keys_title.setFont(font(FONT_MONO, 8.0, 500, 1.2))
        self.body.addWidget(keys_title)
        keys = [
            ("Enter", "发送（输入法组字时不会误发）"),
            ("Shift+Enter", "换行"),
            ("Esc", "停止生成，已收到的内容保留"),
            ("Alt+↑ / Alt+↓", "在同一分叉的兄弟分支间切换"),
            ("Alt+← / Alt+→", "回到上一个节点 / 沿首选分支向下"),
            ("Ctrl+N / Ctrl+L / Ctrl+M", "新对话 / 对话库 / ATLAS"),
        ]
        for k, v in keys:
            row = QHBoxLayout()
            kl = QLabel(k, self.panel)
            kl.setFixedWidth(190)
            kl.setFont(font(FONT_MONO, 8.4))
            kl.setStyleSheet(f"color:{C.brass_dim}; background:transparent;")
            vl = QLabel(v, self.panel)
            vl.setFont(font(FONT_UI, 9.0))
            vl.setStyleSheet(f"color:{C.muted}; background:transparent;")
            row.addWidget(kl)
            row.addWidget(vl, 1)
            self.body.addLayout(row)

        self.body.addStretch(1)
        foot = QHBoxLayout()
        ver = QLabel(f"Delta 0.1  ·  Python {platform.python_version()}  ·  PySide6", self.panel)
        ver.setFont(font(FONT_MONO, 7.8))
        ver.setStyleSheet(f"color:{C.faint}; background:transparent;")
        foot.addWidget(ver)
        foot.addStretch(1)
        close = PillButton("关闭", self.panel)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        self.body.addLayout(foot)

    def _key_text(self, present: bool, length: int) -> str:
        if present:
            return f"DEEPSEEK_API_KEY   ● 已就绪  ·  {length} 字符"
        return "DEEPSEEK_API_KEY   ○ 未设置"

    def _reload(self) -> None:
        ok = self._reload_cb()
        key_len = len(os.environ.get("DEEPSEEK_API_KEY", ""))
        self._key_label.setText(self._key_text(ok, key_len))
        self._key_label.setStyleSheet(f"color:{C.sage if ok else C.danger}; background:transparent;")


class ConfirmDialog(SheetBase):
    def __init__(self, parent, *, title: str, detail: str, confirm: str = "删除", danger: bool = True) -> None:
        super().__init__(parent, 470, 180)
        self.body.setSpacing(10)
        self.add_header("DELTA / CONFIRM", title)
        label = QLabel(detail, self.panel)
        label.setObjectName("SheetText")
        label.setFont(font(FONT_UI, 9.4))
        label.setWordWrap(True)
        self.body.addWidget(label)
        self.body.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = PillButton("取消", self.panel)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = PillButton(confirm, self.panel, accent=True)
        if danger:
            ok.setStyleSheet(
                f"QPushButton {{ background:{C.danger}; color:#160D0B; border:none; border-radius:6px; "
                f"padding:4px 11px; font-size:9pt; font-weight:600; }}"
                f"QPushButton:hover {{ background:#E68B76; }}"
            )
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self.body.addLayout(row)