"""Top bar: title, quiet statistics, and a live hairline while streaming."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget

from .icons import draw_icon
from .theme import C, FONT_MONO, FONT_UI, color, font
from .widgets import IconButton


class EditableTitle(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


class TitleEdit(QLineEdit):
    cancelled = Signal()
    committed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._escaped = False
        self.setFont(font(FONT_UI, 16.5, 600))
        self.setFixedHeight(30)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._escaped = True
            self.cancelled.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.committed.emit(self.text())
            event.accept()
            return
        super().keyPressEvent(event)


class Header(QWidget):
    renamed = Signal(str)
    open_data = Signal()
    shortcuts = Signal()
    about = Signal()
    menu_requested = Signal()
    reset_requested = Signal()
    converge_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(74)
        self._busy = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(32, 10, 20, 8)
        lay.setSpacing(10)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        self.title = EditableTitle("未命名对话", self)
        self.title.setFont(font(FONT_UI, 16.5, 600))
        self.title.setStyleSheet(f"color:{C.ink}; background:transparent;")
        self.title.setToolTip("双击重命名")
        self.title.setMinimumWidth(120)
        self.title.double_clicked.connect(self.begin_rename)
        left.addWidget(self.title, 0, Qt.AlignmentFlag.AlignLeft)

        self.title_edit = TitleEdit(self)
        self.title_edit.setStyleSheet(
            f"QLineEdit {{ background:{C.panel_hi}; border:1px solid {C.line_hi}; border-radius:6px; "
            f"padding:2px 8px; color:{C.ink}; font-size:16.5pt; }}"
        )
        self.title_edit.setVisible(False)
        self.title_edit.committed.connect(self._commit_rename)
        self.title_edit.cancelled.connect(self._cancel_rename)
        left.addWidget(self.title_edit, 0, Qt.AlignmentFlag.AlignLeft)

        self.meta = QLabel("", self)
        self.meta.setFont(font(FONT_MONO, 8.3, 400, 0.55))
        self.meta.setStyleSheet(f"color:{C.muted}; background:transparent;")
        left.addWidget(self.meta, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(left, 1)

        self.more = IconButton("more", self, size=32, icon_size=18, tip="更多", color_=C.muted)
        self.more.clicked.connect(self._menu)
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_meta(self, title: str, nodes: int, forks: int, depth: int) -> None:
        if not self.title_edit.isVisible():
            self.title.setText(title or "未命名对话")
        if nodes == 0:
            self.meta.setText("等待第一条消息")
            return
        parts = [f"{nodes} 节点", f"{forks} 分叉", f"路径 {depth}"]
        self.meta.setText("  ·  ".join(parts))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
            self.update()

    def _tick(self) -> None:
        self._phase += 0.018
        self.update()

    def begin_rename(self) -> None:
        self.title_edit.setText(self.title.text())
        self.title.setVisible(False)
        self.title_edit.setVisible(True)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _commit_rename(self, text: str) -> None:
        if not self.title_edit.isVisible():
            return
        self.title_edit.setVisible(False)
        self.title.setVisible(True)
        if text.strip():
            self.renamed.emit(text.strip())

    def _cancel_rename(self) -> None:
        self._end_rename()
        self.title.setVisible(True)
        self.title_edit.setVisible(False)

    def _end_rename(self) -> None:
        self.title_edit.blockSignals(True)
        self.title_edit.setVisible(False)
        self.title_edit.blockSignals(False)
        self.title.setText(self.title.text())

    def _menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("重命名", self.begin_rename)
        menu.addAction("在资源管理器中打开数据目录", self.open_data)
        menu.addSeparator()
        menu.addAction("回到默认路线", lambda *_: self.reset_requested.emit())
        menu.addAction("把捷径写进默认…", lambda *_: self.converge_requested.emit())
        menu.addSeparator()
        menu.addAction("键盘快捷键", self.shortcuts)
        menu.addAction("关于 Delta", self.about)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), color(C.bg))
        p.setPen(QPen(color(C.line_soft), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        if self._busy:
            grad = QLinearGradient(0, 0, self.width(), 0)
            stop = (self._phase % 1.0)
            grad.setColorAt(0.0, color(C.brass, 0))
            grad.setColorAt(max(0.01, stop - 0.25), color(C.brass, 0))
            grad.setColorAt(stop, color(C.brass, 200))
            grad.setColorAt(min(0.99, stop + 0.25), color(C.brass, 0))
            grad.setColorAt(1.0, color(C.brass, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRect(QRectF(0, self.height() - 2, self.width(), 2))
        p.end()