"""A selectable, auto-sizing rich text body.  No internal scrollbars."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QMenu, QTextBrowser

from .markdown_render import DOC_CSS, markdown_to_html, strip_for_stream
from .theme import C, FONT_UI, font


class RichText(QTextBrowser):
    typing_started = Signal()

    def __init__(self, parent=None, *, size_pt: float = 11.0, color: str = C.ink, selectable: bool = True, css_extra: str = "") -> None:
        super().__init__(parent)
        self._size_pt = size_pt
        self._color = color
        self._css = DOC_CSS + (css_extra or "")
        self._streaming = False
        self._last_text = None
        self._stream_display = ""
        self._stream_cursor: QTextCursor | None = None
        self._doc_width = 0.0
        self._base_fmt: QTextCharFormat | None = None
        self._syncing = False
        self.setObjectName("RichText")
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(0)
        self.document().setDefaultFont(font(FONT_UI, size_pt))
        self.document().setDefaultStyleSheet(self._css)
        self.setOpenExternalLinks(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        if selectable:
            self.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 0; margin: 0; }"
        )

    def set_text(self, text: str, *, streaming: bool = False, force: bool = False) -> None:
        if not force and text == self._last_text and streaming == self._streaming:
            return
        self._last_text = text
        self._streaming = streaming
        if streaming:
            self._set_stream_text(text)
        else:
            self._stream_cursor = None
            self._stream_display = ""
            self._doc_width = 0.0
            self.document().setDefaultFont(font(FONT_UI, self._size_pt))
            self.document().setDefaultStyleSheet(self._css)
            self.setHtml(markdown_to_html(text))
            self._polish()
        self._sync_height()

    def _base_format(self) -> QTextCharFormat:
        if self._base_fmt is None:
            fmt = QTextCharFormat()
            fmt.setFont(font(FONT_UI, self._size_pt))
            fmt.setForeground(QColor(self._color))
            self._base_fmt = fmt
        return self._base_fmt

    def _set_stream_text(self, text: str) -> None:
        display = strip_for_stream(text)
        if self._stream_cursor is not None and display.startswith(self._stream_display):
            delta = display[len(self._stream_display):]
            if delta:
                self._stream_cursor.insertText(delta, self._base_format())
                self._stream_display = display
            return
        self.document().clear()
        self.document().setDocumentMargin(0)
        self._doc_width = 0.0
        self._stream_display = display
        self._stream_cursor = QTextCursor(self.document())
        self._stream_cursor.insertText(display, self._base_format())

    def _polish(self) -> None:
        doc = self.document()
        doc.setDocumentMargin(0)
        cursor = QTextCursor(doc)
        block = doc.begin()
        while block.isValid():
            fmt = block.blockFormat()
            changed = False
            try:
                fmt.setLineHeight(166, QTextBlockFormat.LineHeightTypes.ProportionalHeight)
                changed = True
            except Exception:
                pass
            bg = fmt.background()
            is_code = bg.style() != Qt.BrushStyle.NoBrush and bg.color().lightness() < 80
            if is_code:
                fmt.setTopMargin(0)
                fmt.setBottomMargin(0)
                fmt.setLeftMargin(0)
                fmt.setRightMargin(0)
                try:
                    fmt.setLineHeight(136, QTextBlockFormat.LineHeightTypes.ProportionalHeight)
                except Exception:
                    pass
                changed = True
            else:
                text = block.text()
                if text:
                    probe = QTextCursor(doc)
                    probe.setPosition(block.position() + 1)
                    if probe.charFormat().fontPointSize() >= 14:
                        fmt.setTopMargin(14 if block.blockNumber() else 0)
                        fmt.setBottomMargin(7)
                        changed = True
            if changed:
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            block = block.next()

    def _sync_height(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            w = self.viewport().width()
            if w < 20:
                w = max(120, self.width() - 2)
            if abs(w - self._doc_width) > 0.5:
                self.document().setTextWidth(w)
                self._doc_width = w
            height = self.document().size().height()
            target = max(18, int(math.ceil(height)) + 2)
            if abs(self.height() - target) > 1:
                self.setFixedHeight(target)
        finally:
            self._syncing = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def wheelEvent(self, event) -> None:
        event.ignore()

    def _menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        act_copy_sel = menu.addAction("复制选中")
        act_copy_all = menu.addAction("复制全文")
        menu.addSeparator()
        act_select = menu.addAction("全选")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_copy_sel:
            self.copy()
        elif chosen is act_copy_all:
            QApplication.clipboard().setText(self.toPlainText())
        elif chosen is act_select:
            self.selectAll()

    def keyPressEvent(self, event) -> None:
        if event.matches(Qt.KeySequence.StandardKey.Copy):
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.typing_started.emit()
            event.ignore()
            return
        super().keyPressEvent(event)