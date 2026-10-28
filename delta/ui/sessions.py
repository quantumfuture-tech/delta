"""The library: every conversation is a document, not a chat room."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.model import one_line
from ..core.store import Store, StoreError
from .dialogs import ConfirmDialog, SheetBase
from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import IconButton, PillButton


def _stamp(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts)
    except (OSError, ValueError):
        return ""
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.year == now.year:
        return dt.strftime("%m-%d")
    return dt.strftime("%Y-%m-%d")


class SessionRow(QWidget):
    opened = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, item: dict, *, current: bool, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.current = current
        self._hover = False
        self._trash_hover = False
        self.setFixedHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("打开这份对话" if not current else "当前对话")

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._trash_hover = False
        self.update()

    def mouseMoveEvent(self, e) -> None:
        self._trash_hover = (not self.current) and e.position().x() > self.width() - 44
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if (not self.current) and e.position().x() > self.width() - 44:
            self.delete_requested.emit(str(self.item.get("id")))
            return
        self.opened.emit(str(self.item.get("id")))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.current:
            p.setPen(QPen(color(C.brass_deep, 170), 1))
            p.setBrush(color("#15140F"))
        elif self._hover:
            p.setPen(QPen(color(C.line_hi), 1))
            p.setBrush(color(C.raised))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color(C.line_soft), 1))
        p.drawRoundedRect(r, 10, 10)
        if self.current:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(C.brass))
            p.drawEllipse(QRectF(16, 17, 5, 5))

        x = 30
        p.setFont(font(FONT_UI, 10.6, 600))
        p.setPen(color(C.ink if (self.current or self._hover) else C.ink_soft))
        fm = QFontMetrics(font(FONT_UI, 10.6, 600))
        title = fm.elidedText(str(self.item.get("title") or "未命名对话"), Qt.TextElideMode.ElideRight, self.width() - 140)
        p.drawText(QRectF(x, 10, self.width() - 130, 22), Qt.AlignmentFlag.AlignVCenter, title)

        p.setFont(font(FONT_MONO, 7.8, 400, 0.4))
        p.setPen(color(C.faint))
        meta = f"{self.item.get('nodes', 0)} 节点 · {self.item.get('forks', 0)} 分叉  ·  {_stamp(float(self.item.get('updated_at') or 0))}"
        p.drawText(QRectF(x, 32, self.width() - 130, 16), Qt.AlignmentFlag.AlignVCenter, meta)

        p.setFont(font(FONT_UI, 8.8))
        p.setPen(color(C.muted))
        fm2 = QFontMetrics(font(FONT_UI, 8.8))
        preview = fm2.elidedText(str(self.item.get("preview") or "空对话"), Qt.TextElideMode.ElideRight, self.width() - 160)
        p.drawText(QRectF(x, 48, self.width() - 130, 18), Qt.AlignmentFlag.AlignVCenter, preview)

        if self._hover and not self.current:
            draw_icon(
                p,
                "trash",
                QRectF(self.width() - 34, 27, 17, 17),
                color(C.danger if self._trash_hover else C.muted),
                1.5,
            )
        p.end()


class SessionsSheet(SheetBase):
    def __init__(self, parent, *, store: Store, current_id: str) -> None:
        super().__init__(parent, 760, 560)
        self.store = store
        self.current_id = current_id
        self.selected_id: str | None = None
        self.new_requested = False
        self._rows: list[SessionRow] = []
        self.body.setSpacing(8)
        self.add_header("DELTA / LIBRARY", "对话库")

        top = QHBoxLayout()
        self._count = QLabel("", self.panel)
        self._count.setFont(font(FONT_MONO, 8.2, 400, 0.5))
        self._count.setStyleSheet(f"color:{C.muted}; background:transparent;")
        top.addWidget(self._count)
        top.addStretch(1)
        new_btn = PillButton("新对话", self.panel, accent=True)
        new_btn.clicked.connect(self._new)
        top.addWidget(new_btn)
        self.body.addLayout(top)

        self.scroll = QScrollArea(self.panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 6, 0)
        self._list.setSpacing(7)
        self._list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self._list_host)
        self.body.addWidget(self.scroll, 1)

        self._empty = QLabel("还没有对话。写下第一条消息，它就会出现在这里。", self.panel)
        self._empty.setFont(font(FONT_UI, 9.2))
        self._empty.setStyleSheet(f"color:{C.muted}; background:transparent;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self._empty)

        foot = QHBoxLayout()
        note = QLabel("当前对话不会被删除；删除会移入数据目录的 trash。", self.panel)
        note.setFont(font(FONT_UI, 8.4))
        note.setStyleSheet(f"color:{C.faint}; background:transparent;")
        foot.addWidget(note)
        foot.addStretch(1)
        close = PillButton("关闭", self.panel)
        close.clicked.connect(self.reject)
        foot.addWidget(close)
        self.body.addLayout(foot)
        self._reload()

    def _reload(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        items = self.store.list()
        for item in items:
            row = SessionRow(item, current=(item.get("id") == self.current_id), parent=self._list_host)
            row.opened.connect(self._open)
            row.delete_requested.connect(self._delete)
            self._list.addWidget(row)
            self._rows.append(row)
        self._count.setText(f"{len(items)} 份对话")
        self._empty.setVisible(not items)

    def _open(self, conversation_id: str) -> None:
        self.selected_id = conversation_id
        self.accept()

    def _new(self) -> None:
        self.new_requested = True
        self.accept()

    def _delete(self, conversation_id: str) -> None:
        if conversation_id == self.current_id:
            return
        item = next((r.item for r in self._rows if r.item.get("id") == conversation_id), None)
        title = str((item or {}).get("title") or "这份对话")
        dlg = ConfirmDialog(self, title="移入回收站？", detail=f"「{title}」会移动到数据目录的 trash，仍可手动找回。", confirm="移入回收站")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.delete(conversation_id)
        except StoreError:
            return
        self._reload()
