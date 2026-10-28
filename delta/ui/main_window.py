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
from ..core import commands, slots
from ..core.model import (
    Conversation,
    DEFAULT_MODEL,
    MODELS,
    LayerLocked,
    ROOT_SCOPE,
    ScopeError,
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
        self.drafts = slots.DraftStore()
        self._binding = False
        self._proposal_job: StreamJob | None = None
        self._proposal_key: slots.SlotKey | None = None
        self._proposal_text: list[str] = []

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
        self._proposal_pump = QTimer(self)
        self._proposal_pump.setInterval(14)
        self._proposal_pump.timeout.connect(self._drain_proposal)

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
        self.transcript.tail_slot_requested.connect(self._open_tail_slot)
        self.transcript.slot_requested.connect(self._open_slot_signal)
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
        self.composer.locate_clicked.connect(self._locate_write_layer)
        self.composer.reset_clicked.connect(self._reset_slot)
        self.composer.mode_clicked.connect(self._slot_mode_menu)
        self.composer.propose_clicked.connect(self._propose_question)

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
        self.drafts = slots.DraftStore.from_ui(convo)
        self._proposal_job = None
        self._proposal_key = None
        state = convo.ui.get("scroll") if restore_scroll else None
        self.transcript.set_conversation(convo, restore=state)
        self.atlas.set_conversation(convo)
        self.header.set_meta(convo.title or "未命名对话", 0, 0, 0)
        self.composer.set_model(convo.model)
        self.composer.set_busy(False)
        self._refresh_stack()
        self._refresh_meta()
        self._bind_slot(load_text=True)
        self._set_atlas(bool(self.settings.get("atlas", True)), save=False)
        self.notice.hide()
        self.settings["last_id"] = convo.id
        self._persist_settings()
        self._save_slots(0)
        QTimer.singleShot(60, lambda: self.transcript.restore(state))

    def _refresh_stack(self) -> None:
        self.stack.setCurrentWidget(self.empty_state if self.convo.is_empty() else self.transcript)

    def _refresh_meta(self) -> None:
        stats = self.convo.stats()
        title = self.convo.title or ("新的对话" if self.convo.is_empty() else "未命名对话")
        self.header.set_meta(title, stats["nodes"], stats["forks"], stats["depth"])
        self.setWindowTitle(f"Delta — {title}" if self.convo.nodes else "Delta")
        self._refresh_slot_ui()

    def _save_slots(self, ms: int = 300) -> None:
        if not self.convo:
            return
        self.convo.ui["slots"] = self.drafts.to_ui()
        self.convo.ui.pop("draft", None)
        self._schedule_save(ms)

    def _bind_slot(self, *, load_text: bool = True) -> None:
        if not getattr(self, "drafts", None):
            return
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.ensure(self.convo, key)
        self._binding = True
        try:
            if load_text:
                self.composer.set_text(draft.text)
        finally:
            self._binding = False
        self._refresh_slot_ui()

    def _refresh_slot_ui(self) -> None:
        if not getattr(self, "drafts", None):
            return
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.ensure(self.convo, key)
        if not draft.dirty:
            initial = slots.initial_text(self.convo, key, draft.mode)
            if draft.text != initial:
                draft.text = initial
            if self.composer.text() != initial:
                self._binding = True
                try:
                    self.composer.set_text(initial)
                finally:
                    self._binding = False
        parent = key.parent_id
        locked = bool(parent and self.convo.is_locked(parent))
        view_ids = {n.id for n in self.convo.view()}
        offscreen = bool(parent and parent not in view_ids)
        target = (
            f"写：{slots.layer_label(self.convo, key)} · "
            f"{slots.base_label(self.convo, key)} · "
            f"{slots.MODE_LABEL[draft.mode]}"
        )
        if locked:
            target += " · 写层已锁 · 执行时先抬基准"
        if offscreen:
            target += " · 不在当前视图"
        can = slots.can_send(self.convo, key, draft)
        reason = slots.send_disabled_reason(self.convo, key, draft)
        modes = slots.allowed_modes(self.convo, key)
        base_user = bool(
            key.base_id is not None
            and key.base_id in self.convo.nodes
            and self.convo.nodes[key.base_id].role == "user"
        )
        self.composer.set_context(len(view_ids))
        self.composer.set_slot_state(
            target,
            offscreen=offscreen,
            locked=locked,
            reset_enabled=draft.dirty and self._proposal_job is None,
            mode_label=f"模式：{slots.MODE_LABEL[draft.mode]}",
            show_mode=len(modes) > 1,
            show_propose=base_user,
            propose_enabled=bool(draft.text.strip()) and not (self._job or self._proposal_job),
            can_send=can,
            send_reason=reason,
            placeholder=slots.MODE_PROMPT[draft.mode],
            verb=slots.MODE_VERB[draft.mode],
            edit_enabled=self._proposal_job is None,
        )
        if not self._job and not self._proposal_job:
            self.composer.set_busy(False)

    def _use_suggestion(self, text: str) -> None:
        key = self.drafts.active_key(self.convo)
        self.drafts.set_text(self.convo, key, text)
        self._binding = True
        try:
            self.composer.set_text(text)
        finally:
            self._binding = False
        self._refresh_slot_ui()
        self._save_slots(0)
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
        if self._binding:
            return
        key = self.drafts.active_key(self.convo)
        self.drafts.set_text(self.convo, key, text)
        self._refresh_slot_ui()
        self._save_slots(450)

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
        if getattr(self, "drafts", None) is not None:
            self.convo.ui["slots"] = self.drafts.to_ui()
            self.convo.ui.pop("draft", None)

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

    # -- slot entrances ------------------------------------------------
    def _open_tail_slot(self, parent_id: str) -> None:
        if parent_id not in self.convo:
            return
        key = slots.SlotKey(parent_id, None)
        self.drafts.open(self.convo, key)
        self._bind_slot(load_text=True)
        self._save_slots(0)
        self.composer.focus_input()

    def _open_slot(self, parent_id: str, base_id: str, mode: str | None = None) -> None:
        if parent_id not in self.convo.nodes or base_id not in self.convo.nodes:
            return
        if self.convo.nodes[base_id].parent_id != parent_id:
            return
        key = slots.SlotKey(parent_id, base_id)
        self.drafts.open(self.convo, key, mode)
        self._bind_slot(load_text=True)
        self._save_slots(0)
        self.composer.focus_input()

    def _open_slot_signal(self, parent_id: str, base_id: str, mode: str) -> None:
        self._open_slot(parent_id, base_id, mode)

    # -- layer actions (all through commands) ---------------------------
    def _activate(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        state = self.transcript.capture_key("l:" + parent_id)
        try:
            commands.set_active(self.convo, parent_id, child_id)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError, ScopeError):
            return
        # Active slot is sticky: changing the view does not retarget the input.
        self.transcript.rebuild(restore=state)
        self._refresh_meta()
        self.atlas.refresh()
        self._schedule_save(120)
        self.composer.focus_input()

    def _close_tab(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.convo or child_id not in self.convo:
            return
        if self.convo.is_locked(parent_id):
            self._ask_unlock(parent_id)
            return
        doomed = self._subtree_ids(child_id)
        count = len(doomed)
        detail = "只删除这一支；其它标签与消息不受影响。此操作不可撤销。"
        if count > 1:
            detail = (
                f"将删除这一支和它的 {count - 1} 条后续。其它标签与消息不受影响。"
                "此操作不可撤销。"
            )
        dlg = ConfirmDialog(self, title="关闭这一支？", detail=detail, confirm="删除", danger=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._job and self._active_node_id in doomed:
            self._stop_generation(silent=True, notify=False)
        if self._proposal_job is not None:
            self._stop_generation(silent=True, notify=False)
        state = self.transcript.capture_key("l:" + parent_id)
        try:
            removed = commands.close(self.convo, parent_id, child_id)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError, ScopeError):
            return
        before = self.drafts.active
        self.drafts.prune(self.convo)
        self.transcript.rebuild(restore=state)
        self._refresh_stack()
        self._refresh_meta()
        self.atlas.refresh()
        if self.drafts.active != before or self.drafts.active is None:
            self._bind_slot(load_text=True)
        else:
            self._refresh_slot_ui()
        self._save_slots(0)
        self._save_now()
        self.toast.show_message(f"已关闭 {removed} 条消息", "ok")

    def _reorder(self, parent_id: str, child_id: str, index: int) -> None:
        try:
            commands.reorder(self.convo, parent_id, child_id, index)
        except LayerLocked:
            self._ask_unlock(parent_id)
            return
        except (KeyError, ValueError, ScopeError):
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
            self._refresh_slot_ui()

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
            menu.addAction("在这一层新建…", lambda *_: self._open_tail_slot(parent_id))
            if node.role == "user":
                menu.addAction(
                    "以这一支为底稿改问",
                    lambda *_: self._open_slot(parent_id, node_id, slots.REVISE_QUESTION),
                )
            else:
                menu.addAction(
                    "按意见改（模型）",
                    lambda *_: self._open_slot(parent_id, node_id, slots.REWRITE_ANSWER),
                )
                menu.addAction(
                    "自己改（不调模型）",
                    lambda *_: self._open_slot(parent_id, node_id, slots.EDIT_ANSWER),
                )
            menu.addSeparator()
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

    # -- slot actions ---------------------------------------------------
    def _locate_write_layer(self) -> None:
        key = self.drafts.active_key(self.convo)
        if key.parent_id is not None and key.parent_id in self.convo.nodes:
            if key.parent_id not in {n.id for n in self.convo.view()}:
                self._set_baseline(key.parent_id)

    def _reset_slot(self) -> None:
        if self._proposal_job is not None:
            return
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.reset(self.convo, key)
        self._binding = True
        try:
            self.composer.set_text(draft.text)
        finally:
            self._binding = False
        self._refresh_slot_ui()
        self._save_slots(0)

    def _slot_mode_menu(self) -> None:
        if self._proposal_job is not None:
            return
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.ensure(self.convo, key)
        modes = slots.allowed_modes(self.convo, key)
        if len(modes) < 2 and draft.snapshot is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{C.panel_hi}; color:{C.ink_dim}; border:1px solid {C.line}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{C.raised}; color:{C.ink}; }}"
            f"QMenu::separator {{ height:1px; background:{C.line}; margin:4px 6px; }}"
        )
        for mode in modes:
            prefix = "✓ " if mode == draft.mode else "   "
            menu.addAction(
                prefix + slots.MODE_LABEL[mode],
                lambda *_, m=mode: self._switch_slot_mode(m),
            )
        if draft.snapshot is not None:
            menu.addSeparator()
            menu.addAction("撤销上次切换", lambda *_: self._undo_slot_mode())
        pos = self.composer._mode_btn.mapToGlobal(self.composer._mode_btn.rect().bottomLeft())
        menu.exec(pos)

    def _switch_slot_mode(self, mode: str) -> None:
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.ensure(self.convo, key)
        if mode == draft.mode:
            return
        if draft.dirty:
            dlg = ConfirmDialog(
                self,
                title="切换模式？",
                detail=(
                    "当前草稿会被新模式替换；切换本身可以撤销一次，"
                    "但之后的编辑、重置或再切一次会让这次撤销过期。"
                ),
                confirm="切换",
                danger=False,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
        try:
            self.drafts.switch_mode(self.convo, key, mode, confirm=True)
        except slots.SlotError:
            return
        self._bind_slot(load_text=True)
        self._save_slots(0)

    def _undo_slot_mode(self) -> None:
        key = self.drafts.active_key(self.convo)
        self.drafts.undo_switch(self.convo, key)
        self._bind_slot(load_text=True)
        self._save_slots(0)

    def _propose_question(self) -> None:
        if self._job or self._proposal_job:
            self.toast.show_message("先等当前生成结束", "error", 1600)
            return
        key = self.drafts.active_key(self.convo)
        if key.base_id is None or key.base_id not in self.convo.nodes:
            return
        if self.convo.nodes[key.base_id].role != "user":
            return
        text = self.composer.text().strip()
        if not text:
            return
        if not self._ensure_key():
            return
        messages = slots.propose_question_messages(self.convo, key, text)
        if not messages:
            return
        self._proposal_job = StreamJob(self.client, messages, self.convo.model)
        self._proposal_job.start()
        self._proposal_key = key
        self._proposal_text = []
        self._proposal_pump.start()
        self.composer.set_busy(True)
        self.composer.set_edit_enabled(False)
        self.header.set_busy(True)
        self._refresh_slot_ui()
        self.notice.hide()

    def _drain_proposal(self) -> None:
        if self._proposal_job is None:
            return
        done: StreamResult | None = None
        while True:
            try:
                item = self._proposal_job.queue.get_nowait()
            except queue.Empty:
                break
            if item[0] == "__done__":
                done = item[1]
                break
            if item[0] == "content":
                self._proposal_text.append(item[1])
        if done is None:
            return
        key = self._proposal_key
        self._proposal_job = None
        self._proposal_key = None
        self._proposal_pump.stop()
        text = "".join(self._proposal_text).strip()
        self._proposal_text = []
        self._set_idle()
        self.composer.set_edit_enabled(True)
        if (
            done.status == "complete"
            and text
            and key is not None
            and slots.slot_valid(self.convo, key)
            and key.base_id is not None
            and key.base_id in self.convo.nodes
            and self.convo.nodes[key.base_id].role == "user"
        ):
            draft = self.drafts.ensure(self.convo, key)
            if draft.mode == slots.REVISE_QUESTION:
                draft.text = text.strip().strip('"').strip("“”").strip()
                draft.dirty = True
                draft.snapshot = None
                if self.drafts.active_key(self.convo) == key:
                    self._bind_slot(load_text=True)
                else:
                    self._refresh_slot_ui()
                self._save_slots(0)
                self.toast.show_message("已替换草稿 · 没有落节点", "ok", 1800)
                return
        if done.status == "error" and done.error is not None:
            self.toast.show_message(f"{error_title(done.error.kind)} · 草稿保留", "error", 2000)
        else:
            self.toast.show_message("改写没有结果 · 草稿保留", "error", 1800)
        self._refresh_slot_ui()

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
        if self._job or self._proposal_job:
            return
        key = self.drafts.active_key(self.convo)
        draft = self.drafts.ensure(self.convo, key)
        if text != draft.text:
            self.drafts.set_text(self.convo, key, text)
        if not slots.can_send(self.convo, key, draft):
            self._refresh_slot_ui()
            return
        needs_model = draft.mode != slots.EDIT_ANSWER
        if needs_model and not self._ensure_key():
            return
        try:
            run = slots.execute_slot(self.convo, key, draft)
        except LayerLocked:
            self._ask_unlock(slots.write_layer(self.convo, key))
            self._refresh_slot_ui()
            return
        except slots.CannotSend:
            self._refresh_slot_ui()
            return
        except (ScopeError, KeyError, ValueError) as exc:
            self.toast.show_message(f"没有写入：{exc}", "error", 2200)
            self._refresh_slot_ui()
            return
        self.drafts.consume(self.convo, key)
        self.drafts.active = slots.default_key(self.convo)
        self.drafts.ensure(self.convo, self.drafts.active)
        self._refresh_stack()
        if run.needs_model and run.assistant_id:
            self._begin_job(run.assistant_id, run.question_id or "", run.messages, run.model)
        else:
            self._set_idle()
        self.transcript.rebuild(keep_bottom=True)
        self.transcript.scroll_to_bottom()
        self._refresh_meta()
        self.atlas.refresh()
        self._bind_slot(load_text=True)
        self._save_slots(0)
        self._save_now()
        if run.write_layer != ROOT_SCOPE:
            viewed = {n.id for n in self.convo.view()}
            layer = self.convo.nodes.get(run.write_layer)
            if layer is not None and run.write_layer not in viewed:
                self.toast.show_message(
                    f"已写入「{one_line(layer.text, 16) or '层'}」· 不在当前视图", "ok", 2200
                )
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
        self._refresh_slot_ui()
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
            key = self.drafts.active_key(self.convo)
            if key.base_id == node_id:
                draft = self.drafts.ensure(self.convo, key)
                if not draft.dirty:
                    self._refresh_slot_ui()
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
        """Remove a placeholder that never received content (a declared repair)."""
        try:
            commands.repair_empty(self.convo, node_id)
        except (LayerLocked, ScopeError):
            node = self.convo.nodes.get(node_id)
            if node is not None:
                self.convo.finish_node(node_id, "error", error_kind="empty")

    def _refresh_parent_layer(self, node_id: str) -> None:
        node = self.convo.nodes.get(node_id)
        if node is not None and node.parent_id:
            self.transcript.refresh_layer(node.parent_id, text_only=True)

    def _stop_generation(self, *, silent: bool = False, notify: bool = True) -> None:
        if self._proposal_job is not None:
            job = self._proposal_job
            self._proposal_job = None
            self._proposal_key = None
            job.cancel.set()
            self._proposal_pump.stop()
            self._proposal_text = []
            self._set_idle()
            self.composer.set_edit_enabled(True)
            self._refresh_slot_ui()
            if notify and not silent:
                self.toast.show_message("已停止改写 · 草稿保留", "ok", 1600)
            return
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
        self._refresh_slot_ui()

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
        self._open_tail_slot(parent_id)
        self.toast.show_message("在这一层按发送重新生成", "ok", 1800)

    def _fix_key_and_retry(self, parent_id: str) -> None:
        self._show_info()
        if self.client.api_key and parent_id in self.convo:
            self.notice.hide()
            self._open_tail_slot(parent_id)
            self.toast.show_message("在这一层按发送重新生成", "ok", 1800)
        elif not self.client.api_key:
            self._missing_key_notice()

    # -- baseline (the only position) ---------------------------------
    def _set_baseline(self, node_id: str) -> None:
        if node_id not in self.convo:
            return
        if self.convo.baseline_id == node_id:
            return
        self.convo.set_baseline(node_id)
        self.transcript.rebuild(keep_bottom=False)
        self.transcript.scroll_to_node(node_id)
        self._refresh_meta()
        self.atlas.refresh()
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
        self._save_slots(0)
        self.toast.show_message(f"已写入 {changed} 处默认 · 回到默认路线后会到同一处", "ok", 2400)

    # -- shutdown ------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._closing = True
        self._stop_generation(silent=True, notify=False)
        self._snapshot_ui()
        self._save_now()
        self._save_window()
        self._pump.stop()
        self._proposal_pump.stop()
        self._save_timer.stop()
        event.accept()