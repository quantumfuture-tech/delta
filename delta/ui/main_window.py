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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.client import ChatClient, StreamError, StreamResult
from ..core.context import chain_messages
from ..core.model import Conversation, DEFAULT_MODEL, MODELS, one_line
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

        self.transcript.fork_requested.connect(self._fork_here)
        self.transcript.delete_requested.connect(self._delete_node)
        self.transcript.retry_requested.connect(self._retry_node)
        self.transcript.focus_requested.connect(self._focus_node)
        self.transcript.switch_requested.connect(self._switch_branch)
        self.transcript.preferred_toggled.connect(self._toggle_preferred)
        self.transcript.fork_here_requested.connect(self._fork_here)
        self.transcript.typing_started.connect(self.composer.focus_input)
        self.transcript.notice.connect(lambda text, kind: self.toast.show_message(text, kind))
        self.transcript.view_moved.connect(self._view_moved)

        self.atlas.node_clicked.connect(self._atlas_click)
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
        bind("Alt+Down", lambda: self._cycle_sibling(1))
        bind("Alt+Up", lambda: self._cycle_sibling(-1))
        bind("Alt+Left", self._go_parent)
        bind("Alt+Right", self._go_preferred)

    # -- conversation lifecycle ----------------------------------------
    def _apply_conversation(self, convo: Conversation, *, restore_scroll: bool = True) -> None:
        self.convo = convo
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
        chain = self.convo.chain()
        pointer = self.convo.pointer()
        kids = self.convo.children(pointer.id) if pointer else []
        elsewhere = bool(
            self._job
            and self._active_node_id
            and self._active_node_id not in {n.id for n in chain}
        )
        self.composer.set_context(len(chain), bool(kids), len(kids), elsewhere)

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

    def _node_in_chain(self, node_id: str) -> bool:
        return any(n.id == node_id for n in self.convo.chain())

    # -- message actions ----------------------------------------------
    def _focus_node(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        if self.convo.pointer_id == node_id:
            return
        self.convo.set_pointer(node_id)
        self.transcript.rebuild(restore=None, keep_bottom=False)
        self.transcript.scroll_to_node(node_id)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(150)

    def _fork_here(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        if self.convo.pointer_id != node_id:
            self.convo.set_pointer(node_id)
            self.transcript.rebuild(restore=None, keep_bottom=False)
            self.transcript.scroll_to_node(node_id)
            self._refresh_meta()
            self.atlas.refresh()
            self._schedule_save(150)
        self._update_context()
        self.composer.focus_input()
        if not self.convo.is_empty() and self.convo.pointer_id == node_id:
            self.composer.set_busy(bool(self._job))

    def _switch_branch(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        state = self.transcript.capture_key("f:" + parent_id)
        target = self.convo.canonical_leaf(child_id)
        self.convo.set_pointer(target)
        self.transcript.rebuild(restore=state)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)
        self.composer.focus_input()

    def _toggle_preferred(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        on = self.convo.toggle_preferred(parent_id, child_id)
        self.transcript.refresh_fork(parent_id)
        self._schedule_save(100)
        self.toast.show_message(
            "已设为首选续写 · 只改动了这一处" if on else "已取消首选续写",
            "ok",
            1800,
        )

    def _delete_node(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        count = self.convo.descendant_count(node_id) + 1
        detail = "只会删除你明确选择的这一支；其它分支与消息不受影响。"
        if count > 1:
            detail = f"将删除这条消息和它的 {count - 1} 条后续。其它分支与消息不受影响。"
        dlg = ConfirmDialog(self, title="删除这一支？", detail=detail, confirm="删除", danger=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._job and self._active_node_id and node_id in self._subtree_ids(self._active_node_id):
            self._stop_generation(silent=True, notify=False)
        removed = self.convo.delete_subtree(node_id)
        self.transcript.rebuild(keep_bottom=True)
        self._refresh_stack()
        self._refresh_meta()
        self.atlas.refresh()
        self._save_now()
        self.toast.show_message(f"已删除 {removed} 条消息", "ok")

    def _retry_node(self, node_id: str) -> None:
        if self._job:
            self.toast.show_message("正在生成，先停止或等待完成", "error", 1600)
            return
        if node_id not in self.convo:
            return
        node = self.convo.nodes[node_id]
        if node.role == "assistant" and node.parent_id:
            parent = self.convo.nodes.get(node.parent_id)
            user_parent = parent
            model = node.model or self.convo.model
        else:
            user_parent = node
            model = self.convo.model
        if user_parent is None:
            return
        if user_parent.role != "user":
            self.toast.show_message("这一支的父节点不是提问，无法直接重试", "error")
            return
        self._begin_assistant(user_parent.id, model=model)

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
        model = self.convo.model
        user = self.convo.add_user(text, model=model, move=True)
        messages = chain_messages(self.convo, user.id)
        self.convo.ui["draft"] = ""
        self.composer.clear()
        if not messages:
            return
        assistant = self.convo.add_assistant(user.id, model=model, move=True)
        self._refresh_stack()
        self._begin_job(assistant.id, user.id, messages, model)
        self.transcript.rebuild(keep_bottom=True)
        self.transcript.scroll_to_bottom()
        self._refresh_meta()
        self.atlas.refresh()
        self._save_now()
        QTimer.singleShot(0, self.transcript.scroll_to_bottom)

    def _begin_assistant(self, user_parent_id: str, *, model: str) -> None:
        if self._job:
            return
        if not self._ensure_key():
            return
        parent = self.convo.nodes.get(user_parent_id)
        if parent is None or parent.role != "user":
            return
        messages = chain_messages(self.convo, user_parent_id)
        if not messages:
            return
        assistant = self.convo.add_assistant(user_parent_id, model=model, move=True)
        self._refresh_stack()
        self._begin_job(assistant.id, user_parent_id, messages, model)
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
                self._refresh_parent_fork(node_id)
        elif result.status == "cancelled":
            self._settle_cancelled(node_id, notify=False)
        else:
            err = result.error or StreamError("server", "unknown error")
            if node is not None and node.text.strip():
                self.convo.finish_node(node_id, "error", error_kind=err.kind, finish_reason=result.finish_reason)
                self.transcript.refresh_node(node_id)
                self._refresh_parent_fork(node_id)
                self.toast.show_message(f"{error_title(err.kind)} · 已保留收到内容", "error", 2200)
            else:
                if node is not None:
                    self.convo.delete_subtree(node_id)
                self.transcript.rebuild(keep_bottom=True)
                self._refresh_stack()
                if parent_id:
                    self.transcript.refresh_fork(parent_id, text_only=True)
                self._show_stream_notice(err, parent_id)

        self._set_idle()
        self._refresh_meta()
        self.atlas.refresh()
        if node_id in {n.id for n in self.convo.chain()}:
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
                self.transcript.refresh_fork(parent_id, text_only=True)
            if notify:
                self.toast.show_message(f"已停止 · 保留 {fmt_count(len(node.text))} 字", "ok", 1800)
            return True
        self.convo.delete_subtree(node_id)
        self.transcript.rebuild(keep_bottom=True)
        if parent_id:
            self.transcript.refresh_fork(parent_id, text_only=True)
        if notify:
            self.toast.show_message("已停止，回答还没有开始", "ok", 1600)
        return False

    def _refresh_parent_fork(self, node_id: str) -> None:
        node = self.convo.nodes.get(node_id)
        if node is not None and node.parent_id:
            self.transcript.refresh_fork(node.parent_id, text_only=True)

    def _stop_generation(self, *, silent: bool = False, notify: bool = True) -> None:
        if not self._job:
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
        self._begin_assistant(parent_id, model=self.convo.model)

    def _fix_key_and_retry(self, parent_id: str) -> None:
        self._show_info()
        if self.client.api_key and parent_id in self.convo:
            self.notice.hide()
            self._begin_assistant(parent_id, model=self.convo.model)
        elif not self.client.api_key:
            self._missing_key_notice()

    # -- navigation ----------------------------------------------------
    def _cycle_sibling(self, step: int) -> None:
        chain = self.convo.chain()
        if not chain:
            return
        pointer = chain[-1]
        anchor_key = None
        target = None
        if self.convo.children(pointer.id):
            kids = self.convo.children(pointer.id)
            ids = [k.id for k in kids]
            pref = pointer.preferred_child if pointer.preferred_child in ids else ids[0]
            idx = ids.index(pref)
            target = ids[(idx + step) % len(ids)]
            anchor_key = "f:" + pointer.id
        else:
            for i in range(len(chain) - 1, 0, -1):
                parent = chain[i - 1]
                kids = self.convo.children(parent.id)
                if len(kids) > 1:
                    ids = [k.id for k in kids]
                    active = chain[i].id
                    if active not in ids:
                        active = ids[0]
                    idx = ids.index(active)
                    target = ids[(idx + step) % len(ids)]
                    anchor_key = "f:" + parent.id
                    break
        if target is None or anchor_key is None:
            return
        state = self.transcript.capture_key(anchor_key)
        self.convo.set_pointer(self.convo.canonical_leaf(target))
        self.transcript.rebuild(restore=state)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)

    def _go_parent(self) -> None:
        pointer = self.convo.pointer()
        if pointer is None or pointer.parent_id is None:
            return
        self.convo.set_pointer(pointer.parent_id)
        self.transcript.rebuild(keep_bottom=False)
        self.transcript.scroll_to_node(pointer.parent_id)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)

    def _go_preferred(self) -> None:
        pointer = self.convo.pointer()
        if pointer is None or not pointer.preferred_child:
            return
        target = self.convo.canonical_leaf(pointer.preferred_child)
        self.convo.set_pointer(target)
        self.transcript.rebuild(keep_bottom=False)
        self.transcript.scroll_to_node(target)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)

    def _atlas_click(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        self.convo.set_pointer(node_id)
        self.transcript.rebuild(keep_bottom=False)
        self.transcript.scroll_to_node(node_id)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)
        self.composer.focus_input()

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