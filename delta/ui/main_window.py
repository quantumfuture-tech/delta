"""Main window: wires the tree, the transcript, the stream and persistence.

Threading contract: the daemon StreamJob never touches the model or widgets.
It only puts deltas into a queue.  The UI drains that queue on a 16ms timer, so
tokens are batched to at most one repaint per frame and a stop button is
immediate even if the socket is silent.
"""
from __future__ import annotations

import base64
import ctypes
import os
import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.client import ChatClient, StreamError, StreamResult
from ..core.context import chain_messages
from ..core.model import (
    Conversation,
    DEFAULT_MODEL,
    MODELS,
    LayerLocked,
    SendBlocked,
    one_line,
)
from ..core.store import Store, StoreError
from .atlas import AtlasView
from .composer import Composer
from .dialogs import ConfirmDialog, InfoSheet
from .empty import EmptyState
from .header import Header
from .message import error_detail, error_title
from .notice import NoticeBar
from .rail import Rail
from .theme import C, FONT_UI, color, font
from .transcript import TranscriptView
from .widgets import Toast, fmt_count
from .worker import StreamJob


def _read_user_env_key() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")
            return str(value).strip()
    except Exception:
        return ""


class MainWindow(QMainWindow):
    def __init__(self, store: Store, settings: dict, convo: Conversation) -> None:
        super().__init__()
        self.store = store
        self.settings = settings
        self.convo = convo
        self.client = ChatClient()
        if not self.client.api_key:
            key = _read_user_env_key()
            if key:
                os.environ["DEEPSEEK_API_KEY"] = key
                self.client = ChatClient()

        self._job: StreamJob | None = None
        self._active_node_id: str | None = None
        self._active_parent_id: str | None = None
        self._stream_started = 0.0
        self._closing = False
        self._first_show = True
        self._pending_parent_id: str | None = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_now)
        self._pump = QTimer(self)
        self._pump.setInterval(14)
        self._pump.timeout.connect(self._drain)
        self._stream_save = QTimer(self)
        self._stream_save.setInterval(700)
        self._stream_save.timeout.connect(self._save_now)

        self.setMinimumSize(1000, 680)
        self.setWindowTitle("Delta")
        self._build()
        self._connect()
        self._shortcuts()
        self._restore_window_geometry()
        self._apply_conversation(convo, restore_scroll=True)
        self._layout_widths()
        if not self.client.api_key:
            QTimer.singleShot(400, self._missing_key_notice)

    # -- construction --------------------------------------------------
    def _build(self) -> None:
        root = QWidget(self)
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.rail = Rail(root)
        outer.addWidget(self.rail)

        self.center = QWidget(root)
        self.center.setObjectName("Center")
        self.center.setStyleSheet("QWidget#Center { background: transparent; }")
        cl = QVBoxLayout(self.center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        outer.addWidget(self.center, 1)

        self.header = Header(self.center)
        cl.addWidget(self.header)

        self.stack = QStackedWidget(self.center)
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.transcript = TranscriptView(self.stack)
        self.empty_state = EmptyState(self.stack)
        self.stack.addWidget(self.transcript)
        self.stack.addWidget(self.empty_state)
        cl.addWidget(self.stack, 1)

        self.notice_host = QWidget(self.center)
        nh = QHBoxLayout(self.notice_host)
        nh.setContentsMargins(0, 6, 0, 0)
        nh.setSpacing(0)
        nh.addStretch(1)
        self.notice = NoticeBar(self.notice_host)
        nh.addWidget(self.notice, 0, Qt.AlignmentFlag.AlignHCenter)
        nh.addStretch(1)
        cl.addWidget(self.notice_host, 0, Qt.AlignmentFlag.AlignHCenter)

        self.composer_host = QWidget(self.center)
        ch = QHBoxLayout(self.composer_host)
        ch.setContentsMargins(44, 10, 0, 16)
        ch.setSpacing(0)
        ch.addStretch(1)
        self.composer = Composer(self.composer_host)
        ch.addWidget(self.composer, 0, Qt.AlignmentFlag.AlignHCenter)
        ch.addStretch(1)
        cl.addWidget(self.composer_host, 0, Qt.AlignmentFlag.AlignHCenter)

        self.atlas_panel = QWidget(root)
        self.atlas_panel.setObjectName("AtlasPanel")
        self.atlas_panel.setFixedWidth(258)
        al = QVBoxLayout(self.atlas_panel)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        self.atlas = AtlasView(self.atlas_panel)
        al.addWidget(self.atlas)
        outer.addWidget(self.atlas_panel)

        self.toast = Toast(self)
        self.notice_host.setVisible(True)

    def _connect(self) -> None:
        self.rail.new_requested.connect(self._new_conversation)
        self.rail.sessions_requested.connect(self._open_sessions)
        self.rail.atlas_toggled.connect(self._set_atlas)
        self.rail.settings_requested.connect(self._show_info)
        self.header.renamed.connect(self._rename)
        self.header.open_data.connect(self._open_data_dir)
        self.header.shortcuts.connect(self._show_info)
        self.header.about.connect(self._show_info)

        self.transcript.activate_requested.connect(self._activate)
        self.transcript.new_requested.connect(self._new_at_layer)
        self.transcript.close_requested.connect(self._close_tab)
        self.transcript.reorder_requested.connect(self._reorder)
        self.transcript.baseline_requested.connect(self._set_baseline)
        self.transcript.menu_requested.connect(self._card_menu)
        self.transcript.typing_started.connect(self.composer.focus_input)
        self.transcript.notice.connect(lambda text, kind: self.toast.show_message(text, kind))
        self.transcript.view_moved.connect(self._view_moved)

        self.header.reset_requested.connect(self._reset_baseline)
        self.header.converge_requested.connect(self._converge)
        self.atlas.node_clicked.connect(self._set_baseline)
        self.empty_state.suggestion.connect(self._use_suggestion)
        self.composer.submit.connect(self._send)
        self.composer.stop.connect(self._stop_generation)
        self.composer.draft_changed.connect(self._draft_changed)
        self.composer.model_changed.connect(self._model_changed)

    def _shortcuts(self) -> None:
        def bind(seq: str, fn) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(fn)

        bind("Ctrl+N", self._new_conversation)
        bind("Ctrl+L", self._open_sessions)
        bind("Ctrl+M", lambda: self._set_atlas(not self.atlas_panel.isVisible()))

    # -- conversation lifecycle ----------------------------------------
    def _apply_conversation(self, convo: Conversation, *, restore_scroll: bool = True) -> None:
        self.convo = convo
        self._pending_parent_id = None
        state = convo.ui.get("scroll") if restore_scroll else None
        self.transcript.set_conversation(convo, restore=state)
        self.atlas.set_conversation(convo)
        self.header.set_meta(convo.title or "未命名对话", 0, 0, 0)
        self.composer.set_model(convo.model)
        self.composer.set_text(str(convo.ui.get("draft", "")))
        self.composer.set_busy(False)
        self._refresh_stack()
        self._refresh_meta()
        self._update_context()
        self._set_atlas(bool(self.settings.get("atlas", True)), save=False)
        self.notice.hide()
        self.settings["last_id"] = convo.id
        self._persist_settings()
        QTimer.singleShot(60, lambda: self.transcript.restore(state))

    def _refresh_stack(self) -> None:
        self.stack.setCurrentWidget(self.empty_state if self.convo.is_empty() else self.transcript)

    def _refresh_meta(self) -> None:
        stats = self.convo.stats()
        title = self.convo.title or ("新的对话" if self.convo.is_empty() else "未命名对话")
        self.header.set_meta(title, stats["nodes"], stats["forks"], stats["depth"])
        self.setWindowTitle(f"Delta — {title}" if self.convo.nodes else "Delta")
        self._update_context()

    def _update_context(self) -> None:
        view = self.convo.view()
        leaf_id = self.convo.view_leaf()
        leaf = self.convo.nodes.get(leaf_id) if leaf_id else None
        layer_count = len(self.convo.children(leaf.id)) if leaf else 0
        pending_title = ""
        if self._pending_parent_id and self._pending_parent_id in self.convo:
            pending_title = one_line(self.convo.nodes[self._pending_parent_id].text, 16)
        elsewhere = bool(
            self._job
            and self._active_node_id
            and self._active_node_id not in {n.id for n in view}
        )
        self.composer.set_context(
            len(view),
            layer_count,
            leaf.role if leaf else "",
            pending_title,
            elsewhere,
        )

    def _use_suggestion(self, text: str) -> None:
        self.composer.set_text(text)
        self.composer.focus_input()

    def _new_conversation(self) -> None:
        if self.convo.is_empty():
            self.composer.focus_input()
            return
        self._stop_generation(silent=True, notify=False)
        self._snapshot_ui()
        self._save_now()
        convo = Conversation()
        try:
            self.store.save(convo)
        except StoreError as exc:
            self.toast.show_message(f"无法保存新对话：{exc}", "error")
        self._apply_conversation(convo)
        self.composer.focus_input()

    def _open_conversation(self, conversation_id: str) -> None:
        if conversation_id == self.convo.id:
            return
        self._stop_generation(silent=True, notify=False)
        self._snapshot_ui()
        self._save_now()
        try:
            convo = self.store.load(conversation_id)
        except StoreError as exc:
            self.toast.show_message(f"打不开这份对话：{exc}", "error")
            return
        self._apply_conversation(convo)
        self.toast.show_message("已切换对话", "ok")

    def _persist_settings(self) -> None:
        try:
            self.store.write_settings(self.settings)
        except StoreError:
            pass

    # -- persistence ---------------------------------------------------
    def _schedule_save(self, ms: int = 300) -> None:
        if self._closing:
            return
        self._save_timer.start(ms)

    def _save_now(self) -> None:
        if not self.convo:
            return
        try:
            self.store.save(self.convo)
        except StoreError as exc:
            self.toast.show_message(f"保存失败：{exc}", "error")

    def _view_moved(self, state: dict) -> None:
        self.convo.ui["scroll"] = state
        self._schedule_save(320)

    def _draft_changed(self, text: str) -> None:
        self.convo.ui["draft"] = text
        self._schedule_save(450)

    def _restore_window_geometry(self) -> None:
        geo = self.settings.get("geometry")
        restored = False
        if geo:
            try:
                restored = self.restoreGeometry(QByteArray.fromBase64(str(geo).encode("ascii")))
            except Exception:
                restored = False
        if not restored:
            screen = QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            w, h = 1420, 900
            if avail:
                w = min(w, max(1000, avail.width() - 80))
                h = min(h, max(680, avail.height() - 80))
            self.resize(w, h)
            if avail:
                self.move(avail.center() - self.rect().center())

    def _save_window(self) -> None:
        try:
            self.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.settings["atlas"] = self.atlas_panel.isVisible()
            self.settings["last_id"] = self.convo.id
            self.store.write_settings(self.settings)
        except Exception:
            pass

    # -- layout --------------------------------------------------------
    def _layout_widths(self) -> None:
        avail = max(420, self.center.width() - 64)
        total = min(890, avail)
        self.composer_host.setFixedWidth(total)
        self.composer.setFixedWidth(total - 44)
        self.notice_host.setFixedWidth(total)
        self.notice.setFixedWidth(total - 44)
        self.toast.reposition()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_widths()
        self.toast.reposition()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            self._layout_widths()
            self._apply_dark_title()
            state = self.convo.ui.get("scroll")
            QTimer.singleShot(80, lambda: self.transcript.restore(state))
            QTimer.singleShot(120, self.composer.focus_input)

    def _apply_dark_title(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            value = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
            caption = ctypes.c_int(0x00100C0A)
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
            border = ctypes.c_int(0x00352A23)
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        except Exception:
            pass

    # -- header / settings --------------------------------------------
    def _rename(self, title: str) -> None:
        self.convo.rename(title)
        self._refresh_meta()
        self._schedule_save(120)

    def _model_changed(self, model: str) -> None:
        if model not in MODELS:
            return
        self.convo.set_model(model)
        self.composer.set_model(model)
        self._schedule_save(120)
        self.toast.show_message("下一条消息将使用新的模型", "ok", 1500)

    def _set_atlas(self, on: bool, *, save: bool = True) -> None:
        self.atlas_panel.setVisible(on)
        self.rail.set_atlas(on)
        self.settings["atlas"] = on
        if save:
            self._persist_settings()
        QTimer.singleShot(0, self._layout_widths)

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))

    def _reload_key(self) -> bool:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            key = _read_user_env_key()
            if key:
                os.environ["DEEPSEEK_API_KEY"] = key
        self.client = ChatClient()
        if self.client.api_key:
            self.notice.hide()
            self.toast.show_message("已读取到密钥", "ok")
            return True
        self.toast.show_message("仍然没有找到 DEEPSEEK_API_KEY", "error")
        return False

    def _show_info(self) -> None:
        sheet = InfoSheet(
            self,
            data_dir=self.store.root,
            key_present=bool(self.client.api_key),
            key_len=len(self.client.api_key),
            model=self.convo.model,
            reload_cb=self._reload_key,
        )
        sheet.exec()

    def _snapshot_ui(self) -> None:
        if self.convo is None:
            return
        try:
            self.convo.ui["scroll"] = self.transcript.capture()
        except Exception:
            pass
        self.convo.ui["draft"] = self.composer.text()

    def _open_sessions(self) -> None:
        self._snapshot_ui()
        self._save_now()
        try:
            from .sessions import SessionsSheet
        except Exception:
            self.toast.show_message("对话库暂时不可用", "error")
            return
        sheet = SessionsSheet(self, store=self.store, current_id=self.convo.id)
        code = sheet.exec()
        if code == 1:
            if sheet.new_requested:
                self._new_conversation()
            elif sheet.selected_id:
                self._open_conversation(sheet.selected_id)

    # -- helpers -------------------------------------------------------
    def _subtree_ids(self, node_id: str) -> set[str]:
        if node_id not in self.convo:
            return set()
        out: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            out.add(cur)
            stack.extend(c.id for c in self.convo.children(cur))
        return out

    # -- layer actions (all go through the four gated core methods) ----
    def _activate(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        if self.convo.is_locked(parent_id):
            self._ask_unlock(parent_id)
            return
        state = self.transcript.capture_key("l:" + parent_id)
        try:
            self.convo.activate(parent_id, child_id)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError):
            return
        self._pending_parent_id = None
        self.transcript.rebuild(restore=state)
        self._refresh_meta()
        self.atlas.refresh()
        self._update_context()
        self._schedule_save(120)
        self.composer.focus_input()

    def _new_at_layer(self, parent_id: str) -> None:
        if parent_id not in self.convo:
            return
        if self.convo.is_locked(parent_id):
            self._ask_unlock(parent_id)
            return
        node = self.convo.nodes[parent_id]
        if node.role == "user":
            self._begin_answer(parent_id)
            return
        self._pending_parent_id = parent_id
        self._update_context()
        self.composer.focus_input()
        self.composer.set_busy(bool(self._job))

    def _close_tab(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        if self.convo.is_locked(parent_id):
            self._ask_unlock(parent_id)
            return
        doomed = self._subtree_ids(child_id)
        count = len(doomed)
        detail = "只删除这一支；其它标签与消息不受影响。"
        if count > 1:
            detail = f"将删除这一支和它的 {count - 1} 条后续。其它标签与消息不受影响。"
        dlg = ConfirmDialog(self, title="关闭这一支？", detail=detail, confirm="删除", danger=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._job and self._active_node_id in doomed:
            self._stop_generation(silent=True, notify=False)
        state = self.transcript.capture_key("l:" + parent_id)
        try:
            removed = self.convo.close(parent_id, child_id)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError):
            return
        if self._pending_parent_id in doomed:
            self._pending_parent_id = None
        self.transcript.rebuild(restore=state)
        self._refresh_stack()
        self._refresh_meta()
        self.atlas.refresh()
        self._update_context()
        self._save_now()
        self.toast.show_message(f"已关闭 {removed} 条消息", "ok")

    def _reorder(self, parent_id: str, child_id: str, index: int) -> None:
        try:
            self.convo.reorder(parent_id, child_id, index)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError):
            return
        self.transcript.refresh_layer(parent_id)
        self._schedule_save(120)

    def _ask_unlock(self, parent_id: str) -> None:
        if parent_id not in self.convo:
            return
        parent = self.convo.nodes[parent_id]
        label = one_line(parent.text, 22) or "对话开头"
        dlg = ConfirmDialog(
            self,
            title="这一层在捷径链上",
            detail=(
                f"把基准抬到「{label}」之后，这一层的切换、新建、关闭、重排会一起恢复；"
                "视图会按默认重新下落，允许跳。"
            ),
            confirm="抬基准",
            danger=False,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_baseline(parent_id)

    def _card_menu(self, node_id: str, global_pos) -> None:
        node = self.convo.nodes.get(node_id)
        if node is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{C.panel_hi}; color:{C.ink_dim}; border:1px solid {C.line}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{C.raised}; color:{C.ink}; }}"
            f"QMenu::separator {{ height:1px; background:{C.line}; margin:4px 6px; }}"
        )
        menu.addAction("复制", lambda *_: self._copy_node(node_id))
        parent_id = node.parent_id
        if parent_id is not None:
            menu.addSeparator()
            menu.addAction("在这一层新建…", lambda *_: self._new_at_layer(parent_id))
            menu.addAction("关闭这一支…", lambda *_: self._close_tab(parent_id, node_id))
            if self.convo.is_locked(parent_id):
                menu.addAction("这一层已锁 · 抬基准…", lambda *_: self._ask_unlock(parent_id))
        menu.exec(global_pos)

    def _copy_node(self, node_id: str) -> None:
        node = self.convo.nodes.get(node_id)
        if node is None:
            return
        QApplication.clipboard().setText(node.text)
        self.toast.show_message("已复制这条消息", "ok", 1400)

    def _begin_answer(self, parent_id: str) -> None:
        if self._job:
            self.toast.show_message("正在生成，先停止或等待完成", "error", 1600)
            return
        if parent_id not in self.convo:
            return
        if self.convo.is_locked(parent_id):
            self._ask_unlock(parent_id)
            return
        if not self._ensure_key():
            return
        try:
            assistant = self.convo.answer(parent_id)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except ValueError:
            return
        messages = chain_messages(self.convo, parent_id)
        if not messages:
            return
        self._pending_parent_id = None
        self._begin_job(assistant.id, parent_id, messages, self.convo.model)
        self._refresh_stack()
        self.transcript.rebuild(keep_bottom=True)
        self.transcript.scroll_to_bottom()
        self._refresh_meta()
        self.atlas.refresh()
        self._save_now()
        QTimer.singleShot(0, self.transcript.scroll_to_bottom)

    # -- generation ----------------------------------------------------
    def _missing_key_notice(self) -> None:
        self.notice.show_notice(
            "没有找到 DEEPSEEK_API_KEY",
            "运行 .\\run.ps1 会自动从 User 环境变量注入；也可以在这里重新读取。",
            "打开设置",
            self._show_info,
        )

    def _ensure_key(self) -> bool:
        if self.client.api_key:
            return True
        self._reload_key()
        if self.client.api_key:
            return True
        self._missing_key_notice()
        return False

    def _send(self, text: str) -> None:
        if self._job:
            return
        text = text.strip()
        if not text:
            return
        if not self._ensure_key():
            return
        pending = self._pending_parent_id
        model = self.convo.model
        try:
            if pending and pending in self.convo:
                user, assistant = self.convo.ask(pending, text)
            else:
                user, assistant = self.convo.send(text)
        except SendBlocked:
            self._update_context()
            self.toast.show_message("这一支停在你的提问上；用这一层的 ＋ 生成回答", "error", 2400)
            return
        except LayerLocked:
            self._pending_parent_id = None
            self._update_context()
            self.toast.show_message("这一层已锁；没有写入", "error", 2200)
            return
        except (KeyError, ValueError):
            self._pending_parent_id = None
            self._update_context()
            return
        messages = chain_messages(self.convo, user.id)
        if not messages:
            return
        self._pending_parent_id = None
        self.convo.ui["draft"] = ""
        self.composer.clear()
        self._refresh_stack()
        self._begin_job(assistant.id, user.id, messages, model)
        self.transcript.rebuild(keep_bottom=True)
        self.transcript.scroll_to_bottom()
        self._refresh_meta()
        self.atlas.refresh()
        self._save_now()
        QTimer.singleShot(0, self.transcript.scroll_to_bottom)

    def _begin_job(self, assistant_id: str, parent_id: str, messages: list[dict[str, str]], model: str) -> None:
        self._active_node_id = assistant_id
        self._active_parent_id = parent_id
        self._stream_started = time.monotonic()
        self._job = StreamJob(self.client, messages, model)
        self._job.start()
        self.composer.set_busy(True)
        self.header.set_busy(True)
        self._pump.start()
        self._stream_save.start()
        self._update_context()
        self.notice.hide()
        self.composer.focus_input()

    def _drain(self) -> None:
        if not self._job or not self._active_node_id:
            return
        node_id = self._active_node_id
        content: list[str] = []
        reasoning: list[str] = []
        done: StreamResult | None = None
        while True:
            try:
                item = self._job.queue.get_nowait()
            except queue.Empty:
                break
            if item[0] == "__done__":
                done = item[1]
                break
            if item[0] == "content":
                content.append(item[1])
            elif item[0] == "reasoning":
                reasoning.append(item[1])
        node = self.convo.nodes.get(node_id)
        if node is None:
            self._abandon_job()
            return
        if content or reasoning:
            self.convo.append_stream(node_id, "".join(content), "".join(reasoning))
            self.transcript.update_stream(node_id)
        if done is not None:
            self._finish_result(done)

    def _finish_result(self, result: StreamResult) -> None:
        if not self._job or not self._active_node_id:
            return
        node_id = self._active_node_id
        parent_id = self._active_parent_id
        node = self.convo.nodes.get(node_id)
        elapsed = max(0.0, time.monotonic() - self._stream_started)
        self._job = None
        self._pump.stop()
        self._stream_save.stop()
        self._active_node_id = None
        self._active_parent_id = None

        if node is not None:
            if result.text:
                if result.text.startswith(node.text):
                    rest = result.text[len(node.text):]
                else:
                    rest = result.text if len(result.text) > len(node.text) else ""
                if rest and node.status == "streaming":
                    self.convo.append_stream(node_id, rest)
            if result.reasoning:
                if result.reasoning.startswith(node.reasoning):
                    rest = result.reasoning[len(node.reasoning):]
                else:
                    rest = result.reasoning if len(result.reasoning) > len(node.reasoning) else ""
                if rest and node.status == "streaming":
                    self.convo.append_stream(node_id, "", rest)

        if node is not None:
            node.meta["elapsed"] = round(elapsed, 2)
            if result.usage:
                node.meta["usage"] = result.usage
            if result.finish_reason:
                node.meta["finish_reason"] = result.finish_reason
            if result.error:
                node.meta["error"] = result.error.message
                node.meta["error_kind"] = result.error.kind

        if result.status == "complete":
            if node is not None:
                self.convo.finish_node(node_id, "complete", finish_reason=result.finish_reason)
                self.transcript.refresh_node(node_id)
                self._refresh_parent_layer(node_id)
        elif result.status == "cancelled":
            self._settle_cancelled(node_id, notify=False)
        else:
            err = result.error or StreamError("server", "unknown error")
            if node is not None and node.text.strip():
                self.convo.finish_node(node_id, "error", error_kind=err.kind, finish_reason=result.finish_reason)
                self.transcript.refresh_node(node_id)
                self._refresh_parent_layer(node_id)
                self.toast.show_message(f"{error_title(err.kind)} · 已保留收到内容", "error", 2200)
            else:
                self._drop_empty_node(node_id)
                self.transcript.rebuild(keep_bottom=True)
                self._refresh_stack()
                if parent_id:
                    self.transcript.refresh_layer(parent_id, text_only=True)
                self._show_stream_notice(err, parent_id)

        self._set_idle()
        self._refresh_meta()
        self.atlas.refresh()
        if node_id in {n.id for n in self.convo.view()}:
            self.transcript.scroll_to_bottom()
        elif result.status == "complete":
            self.toast.show_message("另一分支已生成完成", "ok", 1800)
        self._save_now()

    def _settle_cancelled(self, node_id: str, *, notify: bool) -> bool:
        node = self.convo.nodes.get(node_id)
        if node is None:
            self.transcript.rebuild(keep_bottom=True)
            return False
        parent_id = node.parent_id
        if node.text.strip() or node.reasoning.strip():
            self.convo.finish_node(node_id, "interrupted")
            self.transcript.refresh_node(node_id)
            if parent_id:
                self.transcript.refresh_layer(parent_id, text_only=True)
            if notify:
                self.toast.show_message(f"已停止 · 保留 {fmt_count(len(node.text))} 字", "ok", 1800)
            return True
        self._drop_empty_node(node_id)
        self.transcript.rebuild(keep_bottom=True)
        if parent_id:
            self.transcript.refresh_layer(parent_id, text_only=True)
        if notify:
            self.toast.show_message("已停止，回答还没有开始", "ok", 1600)
        return False

    def _drop_empty_node(self, node_id: str) -> None:
        """Remove a placeholder that never received content (not a layer close)."""
        self.convo.abandon(node_id)

    def _refresh_parent_layer(self, node_id: str) -> None:
        node = self.convo.nodes.get(node_id)
        if node is not None and node.parent_id:
            self.transcript.refresh_layer(node.parent_id, text_only=True)

    def _stop_generation(self, *, silent: bool = False, notify: bool = True) -> None:
        if not self._job:
            if self._pending_parent_id:
                self._pending_parent_id = None
                self._update_context()
            return
        job = self._job
        node_id = self._active_node_id
        self._job = None
        self._active_node_id = None
        self._active_parent_id = None
        job.cancel.set()
        self._pump.stop()
        self._stream_save.stop()
        if node_id:
            node = self.convo.nodes.get(node_id)
            if node is not None and node.status == "streaming":
                # preserve anything already queued before the cancel is observed
                content: list[str] = []
                reasoning: list[str] = []
                while True:
                    try:
                        item = job.queue.get_nowait()
                    except queue.Empty:
                        break
                    if item[0] == "content":
                        content.append(item[1])
                    elif item[0] == "reasoning":
                        reasoning.append(item[1])
                if content or reasoning:
                    self.convo.append_stream(node_id, "".join(content), "".join(reasoning))
            self._settle_cancelled(node_id, notify=not silent and notify)
        self._set_idle()
        self._refresh_stack()
        self._refresh_meta()
        self.atlas.refresh()
        self._save_now()
        self.composer.focus_input()

    def _abandon_job(self) -> None:
        if self._job:
            self._job.cancel.set()
        self._job = None
        self._active_node_id = None
        self._active_parent_id = None
        self._pump.stop()
        self._stream_save.stop()
        self._set_idle()

    def _set_idle(self) -> None:
        self.composer.set_busy(False)
        self.header.set_busy(False)
        self._stream_save.stop()
        self._update_context()

    def _show_stream_notice(self, err: StreamError, parent_id: str | None) -> None:
        detail = error_detail(err.kind, err.message)
        if err.kind == "rate_limit" and err.retry_after:
            detail = f"服务端建议 {int(err.retry_after)} 秒后重试。{detail}"
        if err.kind in ("auth", "config"):
            if parent_id:
                action = "检查密钥并重试"
                callback = lambda: self._fix_key_and_retry(parent_id)
            else:
                action = "连接设置"
                callback = self._show_info
        elif parent_id:
            action = "重试"
            callback = lambda: self._retry_parent(parent_id)
        else:
            action = None
            callback = None
        self.notice.show_notice(error_title(err.kind), detail, action, callback)

    def _retry_parent(self, parent_id: str) -> None:
        self.notice.hide()
        self._begin_answer(parent_id)

    def _fix_key_and_retry(self, parent_id: str) -> None:
        self._show_info()
        if self.client.api_key and parent_id in self.convo:
            self.notice.hide()
            self._begin_answer(parent_id)
        elif not self.client.api_key:
            self._missing_key_notice()

    # -- baseline (the only position) ---------------------------------
    def _set_baseline(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        if self.convo.baseline_id == node_id:
            return
        self._pending_parent_id = None
        self.convo.set_baseline(node_id)
        self.transcript.rebuild(keep_bottom=False)
        self.transcript.scroll_to_node(node_id)
        self._refresh_meta()
        self.atlas.refresh()
        self._update_context()
        self._schedule_save(120)
        node = self.convo.nodes[node_id]
        self.toast.show_message(f"基准 →「{one_line(node.text, 16)}」· 锁集已更新", "ok", 1800)
        self.composer.focus_input()

    def _reset_baseline(self) -> None:
        root = self.convo.root_id()
        if root is None:
            return
        if self.convo.baseline_id == root:
            self.toast.show_message("已经在默认路线上", "ok", 1400)
            return
        self._set_baseline(root)

    def _converge(self) -> None:
        root = self.convo.root_id()
        if root is None or self.convo.baseline_id in (None, root):
            self.toast.show_message("已经在默认路线上，没有捷径可写", "ok", 1600)
            return
        layers = len(self.convo.shortcut_chain()) - 1
        dlg = ConfirmDialog(
            self,
            title="把捷径写进默认？",
            detail=(
                f"会把 {layers} 层的默认改成你现在看到的这几支；"
                "锁不会因此解开，基准也不动。之后回到默认路线会走到同一处。"
            ),
            confirm="写进默认",
            danger=False,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        changed = self.convo.converge(confirm=True)
        self._schedule_save(120)
        self._refresh_meta()
        self._update_context()
        self.toast.show_message(f"已写入 {changed} 处默认 · 回到默认路线后会到同一处", "ok", 2400)

    # -- shutdown ------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._closing = True
        self._stop_generation(silent=True, notify=False)
        self._snapshot_ui()
        self._save_now()
        self._save_window()
        self._pump.stop()
        self._save_timer.stop()
        event.accept()