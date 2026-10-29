import re
import time
from pathlib import Path

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from delta.core import commands, slots
from delta.core.client import StreamError, StreamResult
from delta.core.model import Conversation
from delta.core.store import Store
import delta.ui.main_window as main_window_module
from delta.ui.main_window import MainWindow
from tests import helpers


class FakeClient:
    api_key = "sk-test"

    def __init__(self, chunks, delay=0.008, error=None):
        self.chunks = chunks
        self.delay = delay
        self.error = error
        self.calls = []

    def stream(self, messages, model, cancel=None, on_delta=None):
        self.calls.append([dict(m) for m in messages])
        text = ""
        for chunk in self.chunks:
            if cancel is not None and cancel.is_set():
                return StreamResult(text=text, status="cancelled")
            on_delta("content", chunk)
            text += chunk
            time.sleep(self.delay)
        if self.error is not None:
            return StreamResult(text=text, status="error", error=self.error)
        return StreamResult(text=text, status="complete", finish_reason="stop")


def pump(app, ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.004)


class BlockingClient:
    """Emits a short prefix, then parks until the job is cancelled.

    The interruption test used to race the wall clock: `FakeClient(delay=0.02)`
    plus a fixed pump, so on a busy machine the worker finished all 40 chunks
    before the pump drained them and "interrupted" came out "complete" (and on a
    quieter one the text overshot its budget instead).  Here the test — not the
    clock — decides when the stop button is pressed.
    """

    api_key = "sk-test"

    def __init__(self, chunks, prefix=3):
        self.chunks = chunks
        self.prefix = prefix
        self.calls = []

    def stream(self, messages, model, cancel=None, on_delta=None):
        self.calls.append([dict(m) for m in messages])
        text = ""
        for index, chunk in enumerate(self.chunks):
            if cancel is not None and cancel.is_set():
                return StreamResult(text=text, status="cancelled")
            on_delta("content", chunk)
            text += chunk
            if index + 1 >= self.prefix:
                while cancel is not None:
                    if cancel.wait(0.005):
                        return StreamResult(text=text, status="cancelled")
        return StreamResult(text=text, status="complete", finish_reason="stop")


def wait_until(app, predicate, timeout=4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.008)
    return predicate()


def wait_idle(app, window, timeout=4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if window._job is None:
            app.processEvents()
            return True
        time.sleep(0.008)
    return False


def wait_proposal(app, window, timeout=4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if window._proposal_job is None:
            app.processEvents()
            return True
        time.sleep(0.008)
    return False


@pytest.fixture
def window(qapp, tmp_path):
    store = Store(tmp_path / "data")
    convo = Conversation(title="ui-test")
    store.save(convo)
    win = MainWindow(store, store.read_settings(), convo)
    win.resize(1280, 800)
    win.show()
    pump(qapp, 120)
    yield win
    win._stop_generation(silent=True, notify=False)
    win.close()
    win.deleteLater()
    pump(qapp, 30)


def type_text(window, text: str) -> None:
    window.composer.set_text(text)
    window._draft_changed(text)


def seed(window, qapp, *, extra=False):
    """root -> a1 -> Q1 -> (B1 | B2); optionally a2 under root."""
    c = window.convo
    u0 = helpers.root(c, "root")
    a1 = helpers.add(c, u0.id, "assistant", "A1")
    a1.text = "A1"
    u1 = helpers.add(c, a1.id, "user", "Q1")
    b1 = helpers.add(c, u1.id, "assistant", "B1")
    b1.text = "B1"
    b2 = helpers.add(c, u1.id, "assistant", "B2")
    b2.text = "B2"
    helpers.activate(c, u1.id, b1.id)
    a2 = None
    if extra:
        a2 = helpers.add(c, u0.id, "assistant", "A2")
        a2.text = "A2"
        helpers.activate(c, u0.id, a1.id)
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
    window.drafts.active = slots.default_key(c)
    window._bind_slot(load_text=True)
    pump(qapp, 80)
    return c, u0, a1, u1, b1, b2, a2


class _DialogStub:
    result = QDialog.DialogCode.Accepted
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _DialogStub.calls.append(kwargs)

    def exec(self):
        return self.result


def test_stream_complete_and_persist(qapp, window):
    window.client = FakeClient(["你", "好", "\n\n## 标题"])
    window._send("测试")
    assert wait_idle(qapp, window)
    chain = window.convo.view()
    assert len(chain) == 2
    assert chain[-1].role == "assistant"
    assert chain[-1].text == "你好\n\n## 标题"
    assert chain[-1].status == "complete"
    assert not window.composer._busy
    saved = window.store.load(window.convo.id)
    assert saved.nodes[chain[-1].id].text == "你好\n\n## 标题"
    assert window.drafts.active_key(window.convo) == slots.SlotKey(chain[-1].id, None)


def test_interrupt_preserves_partial(qapp, window):
    window.client = BlockingClient([str(i) for i in range(40)], prefix=3)
    window._send("long")
    assert wait_until(qapp, lambda: len(window.convo.view()[-1].text) >= 3)
    window._stop_generation()
    assert wait_idle(qapp, window)
    chain = window.convo.view()
    assert window._job is None
    assert len(chain) == 2
    assert chain[-1].status == "interrupted"
    assert 0 < len(chain[-1].text) < 40
    saved = window.store.load(window.convo.id)
    assert saved.nodes[chain[-1].id].status == "interrupted"
    assert saved.nodes[chain[-1].id].text == chain[-1].text


def test_enter_key_sends(qapp, window):
    window.client = FakeClient(["ok"])
    window.composer.focus_input()
    QTest.keyClicks(window.composer.edit, "keyboard")
    QTest.keyClick(window.composer.edit, Qt.Key.Key_Return)
    assert wait_idle(qapp, window)
    assert window.convo.view()[-1].text == "ok"
    assert window.composer.text() == ""


def test_final_markdown_after_streaming(qapp, window):
    from delta.ui.markdown_render import DOC_CSS

    window.client = FakeClient(["## 标题", "\n\n**粗体** 和 " + chr(96) + "代码" + chr(96)])
    window._send("render")
    assert wait_idle(qapp, window)
    node = window.convo.view()[-1]
    card = window.transcript.card_for(node.id)
    assert card is not None
    assert "标题" in card.body.toPlainText()
    assert card.body.document().defaultStyleSheet() == DOC_CSS


def test_error_without_content_leaves_no_half_node(qapp, window):
    window.client = FakeClient([], error=StreamError("auth", "nope", status=401))
    window._send("refuse")
    assert wait_idle(qapp, window)
    chain = window.convo.view()
    assert len(chain) == 1
    assert window.convo.view_leaf() == chain[0].id
    assert all(n.role == "user" for n in window.convo.nodes.values())
    assert window.notice.isVisible()
    assert not window.composer._busy


def test_default_slot_at_a_user_leaf_is_renew_answer(qapp, window):
    c = window.convo
    leaf = helpers.root(c, "only question")
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
    key = window.drafts.active_key(c)
    assert key == slots.SlotKey(leaf.id, None)
    draft = window.drafts.ensure(c, key)
    assert draft.mode == slots.NEW_ANSWER
    assert window.composer._target.mode == "再生成"
    assert slots.can_send(c, key, draft)
    window.client = FakeClient(["answer"])
    window._send("")
    assert wait_idle(qapp, window)
    assert c.view()[-1].text == "answer"


def test_activate_replaces_only_the_suffix(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    before = window.transcript.row_widgets()
    prefix = {k: id(v) for k, v in before.items() if k in ("n:" + u0.id, "l:" + u0.id)}
    window._activate(u0.id, a2.id)
    pump(qapp, 60)
    after = window.transcript.row_widgets()
    assert all(k in after and id(after[k]) == old for k, old in prefix.items())
    assert [n.id for n in c.view()] == [u0.id, a2.id]
    assert b1.id in c.nodes and b2.id in c.nodes and u1.id in c.nodes


def test_switch_back_reuses_cached_cards(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    left_card = window.transcript.card_for(u1.id)
    assert left_card is not None
    window._activate(u0.id, a2.id)
    pump(qapp, 60)
    assert window.transcript.card_for(u1.id) is None
    window._activate(u0.id, a1.id)
    pump(qapp, 60)
    assert window.transcript.card_for(u1.id) is left_card
    assert window.convo.view()[-1].id == b1.id


def test_locked_layer_click_only_opens_the_confirmation(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    window._set_baseline(b2.id)
    pump(qapp, 60)
    assert c.locked_parent_ids() == {u0.id, a1.id, u1.id}
    bar = window.transcript._layers[u0.id]
    chip = next(chip for chip in bar._chips if chip.child.id == a2.id)
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 16))
    pump(qapp, 60)
    assert c.nodes[u0.id].default_child == a1.id
    assert c.baseline_id == b2.id
    assert bar._confirm.isVisible()


def test_confirm_raises_the_baseline_and_releases_the_layer(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    window._set_baseline(b2.id)
    pump(qapp, 60)
    bar = window.transcript._layers[u0.id]
    chip = next(chip for chip in bar._chips if chip.child.id == a2.id)
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 16))
    pump(qapp, 30)
    bar._confirm_ok.click()
    pump(qapp, 80)
    assert c.baseline_id == u0.id
    assert c.locked_parent_ids() == set()
    assert c.nodes[u0.id].default_child == a1.id


def test_unlock_confirmation_works_with_tab_and_enter(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    window._set_baseline(b2.id)
    pump(qapp, 40)
    bar = window.transcript._layers[u0.id]
    bar._show_confirm()
    assert bar._confirm_ok.focusPolicy() == Qt.FocusPolicy.StrongFocus
    bar._confirm_ok.setFocus()
    QTest.keyClick(bar._confirm_ok, Qt.Key.Key_Return)
    pump(qapp, 80)
    assert c.baseline_id == u0.id
    assert c.locked_parent_ids() == set()


def test_close_active_activates_the_next_display_neighbour(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    assert c.nodes[u1.id].default_child == b1.id
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    _DialogStub.result = QDialog.DialogCode.Accepted
    window._close_tab(u1.id, b1.id)
    pump(qapp, 80)
    assert b1.id not in c.nodes
    assert c.nodes[u1.id].default_child == b2.id
    assert c.view()[-1].id == b2.id


def test_close_on_a_locked_layer_asks_for_the_baseline(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._set_baseline(b2.id)
    pump(qapp, 40)
    calls = []
    monkeypatch.setattr(window, "_ask_unlock", lambda pid: calls.append(pid))
    window._close_tab(u1.id, b2.id)
    assert calls == [u1.id]
    assert b2.id in c.nodes


def test_open_tail_slot_shows_write_line_and_reset(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    keep = len(c.nodes)
    window._open_tail_slot(a1.id)
    pump(qapp, 40)
    assert len(c.nodes) == keep
    assert "写：" in window.composer._layer_label.text()
    assert window.composer._target.mode == "新提问"
    assert window.composer._target.number == "＋"
    assert not window.composer._reset_btn.isEnabled()
    type_text(window, "Q2")
    pump(qapp, 30)
    assert window.composer._reset_btn.isEnabled()
    assert window.drafts.ensure(window.convo, window.drafts.active_key(c)).text == "Q2"
    window._reset_slot()
    assert window.composer.text() == ""
    assert not window.composer._reset_btn.isEnabled()


def test_open_base_slot_modes_and_undo(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b1.id, slots.REWRITE_ANSWER)
    pump(qapp, 40)
    assert window.composer._target.mode == "按意见改"
    assert window.composer._target.number == "01"
    assert window.composer._mode_btn.isVisible()
    type_text(window, "shorter")
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    _DialogStub.result = QDialog.DialogCode.Accepted
    window._switch_slot_mode(slots.EDIT_ANSWER)
    pump(qapp, 40)
    assert window.composer._target.mode == "自己改"
    assert window.composer.text() == "B1"
    window._undo_slot_mode()
    pump(qapp, 40)
    assert window.composer._target.mode == "按意见改"
    assert window.composer.text() == "shorter"


def test_target_marker_matches_the_tab_number_and_title(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b2.id, slots.REWRITE_ANSWER)
    pump(qapp, 60)
    assert window.composer._target.number == "02"
    assert window.composer._target.title.startswith("B2")
    assert window.composer._target.mode == "按意见改"
    assert window.composer._layer_label.text().startswith("写：")
    bar = window.transcript._layers[u1.id]
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    assert chip.open is True


def test_per_slot_drafts_persist_when_switching(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_tail_slot(a1.id)
    type_text(window, "tail draft")
    window._open_slot(a1.id, u1.id, slots.REVISE_QUESTION)
    type_text(window, "revised question")
    window._open_tail_slot(a1.id)
    assert window.composer.text() == "tail draft"
    window._open_slot(a1.id, u1.id, slots.REVISE_QUESTION)
    assert window.composer.text() == "revised question"


def test_offview_write_layer_and_locate(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    window._open_slot(a1.id, u1.id, slots.REVISE_QUESTION)
    type_text(window, "Q1 changed")
    window._set_baseline(a2.id)
    pump(qapp, 60)
    line = window.composer._layer_label.text()
    assert "不在当前视图" in line
    assert window.composer._target.offscreen is True
    assert window.composer._locate_btn.isVisible()
    window._locate_write_layer()
    pump(qapp, 60)
    assert c.baseline_id == a1.id
    assert "不在当前视图" not in window.composer._layer_label.text()
    assert window.composer._target.offscreen is False


def test_locked_send_keeps_draft_and_unlocks_without_writing(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra=True)
    window._open_slot(a1.id, u1.id, slots.REVISE_QUESTION)
    type_text(window, "Q1 changed")
    window._set_baseline(b2.id)
    pump(qapp, 40)
    assert "执行时先抬基准" in window.composer._layer_label.text()
    assert window.composer._target.locked is True
    before = len(c.nodes)
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    _DialogStub.result = QDialog.DialogCode.Rejected
    window._send("Q1 changed")
    pump(qapp, 40)
    assert len(c.nodes) == before
    assert window.composer.text() == "Q1 changed"
    assert window.drafts.ensure(c, window.drafts.active_key(c)).text == "Q1 changed"
    _DialogStub.result = QDialog.DialogCode.Accepted
    window._send("Q1 changed")
    pump(qapp, 80)
    assert c.baseline_id == a1.id
    assert len(c.nodes) == before
    assert window.composer.text() == "Q1 changed"
    window.client = FakeClient(["new answer"])
    window._send("Q1 changed")
    assert wait_idle(qapp, window)
    assert len(c.nodes) > before
    assert c.view()[-1].text == "new answer"


def test_new_answer_at_the_answer_tail(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    before = len(c.children(u1.id))
    window._open_tail_slot(u1.id)
    assert window.drafts.ensure(c, window.drafts.active_key(c)).mode == slots.NEW_ANSWER
    window.client = FakeClient(["fresh answer"])
    window._send("")
    assert wait_idle(qapp, window)
    assert len(c.children(u1.id)) == before + 1
    assert c.view()[-1].text == "fresh answer"


def test_execute_moves_active_slot_to_new_view_leaf(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    old_key = slots.SlotKey(a1.id, None)
    window._open_tail_slot(a1.id)
    type_text(window, "Q2")
    window.client = FakeClient(["answer"])
    window._send("Q2")
    assert wait_idle(qapp, window)
    assert window.drafts.active_key(c) == slots.SlotKey(c.view_leaf(), None)
    assert window.drafts.ensure(c, old_key).text == ""


def test_rewrite_empty_is_regenerate(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b1.id, slots.REWRITE_ANSWER)
    window.client = FakeClient(["fresh"])
    window._send("")
    assert wait_idle(qapp, window)
    assert window.client.calls[-1][-1]["content"] == "Q1"
    assert c.view()[-1].text == "fresh"


def test_rewrite_with_feedback_reads_base(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b1.id, slots.REWRITE_ANSWER)
    type_text(window, "shorter")
    window.client = FakeClient(["short"])
    window._send("shorter")
    assert wait_idle(qapp, window)
    contents = [m["content"] for m in window.client.calls[-1]]
    assert "B1" in contents and "shorter" in contents
    assert c.view()[-1].text == "short"


def test_self_edit_answer_has_no_model_call(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b1.id, slots.EDIT_ANSWER)
    type_text(window, "manual answer")
    window.client = FakeClient([])
    window._send("manual answer")
    pump(qapp, 80)
    assert window.client.calls == []
    assert c.view()[-1].text == "manual answer"
    assert c.view()[-1].status == "complete"


def test_propose_question_replaces_draft_only(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(a1.id, u1.id, slots.REVISE_QUESTION)
    type_text(window, "current Q")
    before = len(c.nodes)
    window.client = FakeClient(["Q1 better"])
    window._propose_question()
    assert wait_proposal(qapp, window)
    assert len(c.nodes) == before
    draft = window.drafts.ensure(c, window.drafts.active_key(c))
    assert draft.mode == slots.REVISE_QUESTION
    assert draft.text == "Q1 better"
    assert draft.dirty


def test_clean_draft_disappears_from_ui_slots_after_save(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._open_slot(u1.id, b1.id, slots.EDIT_ANSWER)
    pump(qapp, 40)
    window._save_slots(0)
    clean = c.ui["slots"]
    assert clean["items"] == []
    assert clean["active"] is None
    window._draft_changed("manual answer")
    window._save_slots(0)
    dirty = c.ui["slots"]
    assert len(dirty["items"]) == 1
    assert dirty["active"] is not None
    window._reset_slot()
    window._save_slots(0)
    clean2 = c.ui["slots"]
    assert clean2["items"] == []
    assert clean2["active"] is None


def test_restart_restores_active_slot_and_draft(qapp, tmp_path):
    store = Store(tmp_path / "restart")
    c = Conversation(title="restart")
    u = helpers.root(c, "root")
    a = helpers.add(c, u.id, "assistant", "A")
    store.save(c)
    w = MainWindow(store, store.read_settings(), c)
    w.resize(1200, 800); w.show(); pump(qapp, 160)
    w._open_slot(u.id, a.id, slots.REWRITE_ANSWER)
    type_text(w, "unsent feedback")
    w._save_slots(0); w._save_now()
    w.close(); pump(qapp, 40)
    loaded = store.load(c.id)
    w2 = MainWindow(store, store.read_settings(), loaded)
    w2.resize(1200, 800); w2.show(); pump(qapp, 260)
    key = w2.drafts.active_key(loaded)
    assert key == slots.SlotKey(u.id, a.id)
    assert w2.drafts.ensure(loaded, key).text == "unsent feedback"
    assert w2.composer.text() == "unsent feedback"
    w2.close(); w2.deleteLater(); pump(qapp, 30)


def test_card_has_only_the_copy_action(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    card = window.transcript.card_for(a1.id)
    assert card is not None
    card._set_actions_visible(True)
    assert len(card.action_buttons) == 1
    assert card.action_buttons[0] is card.btn_copy
    assert not hasattr(card, "btn_fork")
    assert not hasattr(card, "btn_delete")
    assert not hasattr(card, "btn_retry")


def test_tabs_stay_on_one_row_and_scroll(qapp, window):
    c = window.convo
    u0 = helpers.root(c, "root")
    kids = []
    for i in range(8):
        kid = helpers.add(c, u0.id, "assistant", f"branch {i}: " + "long preview text, " * 6)
        kids.append(kid)
    helpers.activate(c, u0.id, kids[0].id)
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
    pump(qapp, 150)
    bar = window.transcript._layers[u0.id]
    assert bar is not None
    assert len(bar._chips) == 8
    # the row is title (20) + gap (5) + tab row (33) + padding; assert the shape, not the number
    assert bar._title.isVisible()
    assert bar._scroll.height() == 33
    assert bar.height() == bar._scroll.mapTo(bar, QPoint(0, 0)).y() + 33
    ys = {chip.mapTo(bar, QPoint(0, 0)).y() for chip in bar._chips}
    assert len(ys) == 1
    assert bar._scroll.horizontalScrollBar().maximum() > 0


class _Wheel:
    def __init__(self, dy=0, dx=0, mods=Qt.KeyboardModifier.NoModifier):
        self._angle = QPoint(dx, dy)
        self._mods = mods
        self.accepted = False
        self.ignored = False

    def pixelDelta(self):
        return QPoint()

    def angleDelta(self):
        return self._angle

    def modifiers(self):
        return self._mods

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def test_tab_row_wheel_scrolls_horizontally(qapp, window):
    c = window.convo
    u0 = helpers.root(c, "root")
    kids = []
    for i in range(8):
        kid = helpers.add(c, u0.id, "assistant", f"tab {i}: " + "preview " * 8)
        kids.append(kid)
    helpers.activate(c, u0.id, kids[0].id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta()
    pump(qapp, 160)
    bar = window.transcript._layers[u0.id]
    sb = bar._scroll.horizontalScrollBar()
    assert sb.maximum() > 0
    ev = _Wheel(-120)
    bar._scroll.wheelEvent(ev)
    assert ev.accepted and not ev.ignored
    assert sb.value() > 0
    for _ in range(40):
        bar._scroll.wheelEvent(_Wheel(-120))
    assert sb.value() == sb.maximum()
    last = bar._chips[-1]
    right = last.mapTo(bar._scroll.viewport(), QPoint(last.width(), 0)).x()
    assert right <= bar._scroll.viewport().width() + 2
    ev2 = _Wheel(-120)
    bar._scroll.wheelEvent(ev2)
    assert ev2.accepted and not ev2.ignored and sb.value() == sb.maximum()
    before = sb.value()
    bar._scroll.wheelEvent(_Wheel(+120, mods=Qt.KeyboardModifier.ShiftModifier))
    assert sb.value() < before


def test_tab_plus_opens_that_slot_and_marks_the_tab(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    QTest.mouseClick(
        chip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(chip.width() - 36, 16),
    )
    pump(qapp, 60)
    key = window.drafts.active_key(c)
    assert key == slots.SlotKey(u1.id, b2.id)
    assert window.drafts.ensure(c, key).mode == slots.REWRITE_ANSWER
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    assert chip.open is True
    assert bar._add.open is False
    assert bar._chips[0].open is False


def test_single_child_layer_has_a_visible_tab_with_plus_and_close(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[a1.id]
    assert bar is not None
    # every layer keeps the same row: a title line and a 33px tab row
    assert bar._title.isVisible() and bar._title.text() == "分支 · 1"
    assert bar.height() == bar._scroll.mapTo(bar, QPoint(0, 0)).y() + 33
    assert len(bar._chips) == 1
    assert bar._add.isVisible()
    chip = bar._chips[0]
    QTest.mouseClick(
        chip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(chip.width() - 36, 16),
    )
    pump(qapp, 60)
    assert window.drafts.active_key(c) == slots.SlotKey(a1.id, u1.id)
    assert bar._chips[0].open is True


def test_tail_chip_has_an_open_state(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    QTest.mouseClick(bar._add, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 16))
    pump(qapp, 60)
    assert window.drafts.active_key(c) == slots.SlotKey(u1.id, None)
    assert bar._add.open is True
    assert all(not chip.open for chip in bar._chips)


def test_add_chip_opens_a_slot_without_creating(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    before = len(c.nodes)
    bar = window.transcript._layers[u1.id]
    QTest.mouseClick(bar._add, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 16))
    pump(qapp, 60)
    assert len(c.nodes) == before
    assert window.drafts.active_key(c) == slots.SlotKey(u1.id, None)
    assert window.drafts.ensure(c, window.drafts.active_key(c)).mode == slots.NEW_ANSWER


def test_drag_reorders_the_layer_without_moving_the_view(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    target = next(ch for ch in bar._chips if ch.child.id == b1.id)
    vp = bar._scroll.viewport()

    bar._begin_drag(b2.id, chip.mapToGlobal(QPoint(40, chip.height() // 2)))
    assert bar._drag is not None
    assert chip.isHidden()                      # the lifted tab is a pixmap now
    gx = target.mapTo(vp, QPoint(2, target.height() // 2)).x()
    gp = vp.mapToGlobal(QPoint(gx, target.height() // 2))
    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(bar.mapFromGlobal(gp)), QPointF(gp),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(bar, move)
    pump(qapp, 30)
    assert bar._drag["index"] == 0
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(bar.mapFromGlobal(gp)), QPointF(gp),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(bar, release)
    pump(qapp, 60)
    assert bar._drag is None
    assert c.display_ids(u1.id) == [b2.id, b1.id]
    assert c.nodes[u1.id].default_child == b1.id
    assert c.view()[-1].id == b1.id


def test_drag_escape_puts_the_tab_back(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    before = c.display_ids(u1.id)
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    vp = bar._scroll.viewport()
    bar._begin_drag(b2.id, chip.mapToGlobal(QPoint(40, chip.height() // 2)))
    target = next(ch for ch in bar._chips if ch.child.id == b1.id)
    gx = target.mapTo(vp, QPoint(2, target.height() // 2)).x()
    gp = vp.mapToGlobal(QPoint(gx, target.height() // 2))
    QApplication.sendEvent(
        bar,
        QMouseEvent(
            QEvent.Type.MouseMove, QPointF(bar.mapFromGlobal(gp)), QPointF(gp),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert bar._drag["index"] == 0
    bar._on_escape()
    pump(qapp, 40)
    assert bar._drag is None
    assert c.display_ids(u1.id) == before
    assert bar._chips[0].isVisible()


def test_close_chip_removes_the_branch(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    _DialogStub.result = QDialog.DialogCode.Accepted
    bar = window.transcript._layers[u1.id]
    chip = next(ch for ch in bar._chips if ch.child.id == b2.id)
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(chip.width() - 10, 16))
    pump(qapp, 80)
    assert b2.id not in c.nodes
    assert c.view()[-1].id == b1.id


def test_context_menu_is_the_entrance_for_a_slot(qapp, window, monkeypatch):
    class MenuStub:
        last = None

        def __init__(self, *args, **kwargs):
            self.actions = []
            MenuStub.last = self

        def setStyleSheet(self, text):
            pass

        def addAction(self, text, callback=None):
            self.actions.append(text)

        def addSeparator(self):
            pass

        def exec(self, pos):
            pass

    monkeypatch.setattr(main_window_module, "QMenu", MenuStub)
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._card_menu(a1.id, QPoint(0, 0))
    actions = MenuStub.last.actions
    assert "在这一层新建…" in actions
    assert "按意见改（模型）" in actions
    assert "自己改（不调模型）" in actions
    window._set_baseline(b2.id)
    pump(qapp, 40)
    window._card_menu(a1.id, QPoint(0, 0))
    assert "这一层已锁 · 抬基准…" in MenuStub.last.actions


def test_atlas_click_moves_only_the_baseline(qapp, window):
    c, u0, a1, u1, b1, _b2, _ = seed(window, qapp)
    window.atlas.refresh()
    window.atlas._layout_pixels()
    before_defaults = {u0.id: a1.id, a1.id: u1.id, u1.id: b1.id}
    before_order = {
        u0.id: c.display_ids(u0.id),
        a1.id: c.display_ids(a1.id),
        u1.id: c.display_ids(u1.id),
    }
    count = len(c.nodes)

    QTest.mouseClick(window.atlas, Qt.MouseButton.LeftButton, pos=window.atlas._pix[u1.id].toPoint())
    pump(qapp, 80)

    assert c.baseline_id == u1.id
    assert len(c.nodes) == count
    for pid, child in before_defaults.items():
        active = c.layer(pid).active
        assert (active.id if active else None) == child
    for pid, order in before_order.items():
        assert c.display_ids(pid) == order
    # locks are derived from the shortcut chain, never written by the click
    assert c.is_locked(u0.id) and c.is_locked(a1.id)
    assert c.locked_parent_ids() == {n.parent_id for n in c.shortcut_chain() if n.parent_id}


def test_ui_has_no_direct_layer_writes():
    root = Path(__file__).resolve().parents[1] / "delta" / "ui"
    forbidden = [
        r"\.default_child\s*=(?!=)",
        r"\.baseline_id\s*=(?!=)",
        r"\.display_order\s*=(?!=)",
        r"\.nodes\s*\[[^\]]*\]\s*=(?!=)",
        r"\.nodes\.(pop|clear)\(",
        r"\._write_",
        r"\._writing\(",
        r"\._require_scope\(",
        r"\.create\(\s*(self\.)?convo",
        r"convo\.create\(",
        r"convo\.activate\(",
        r"convo\.close\(",
        r"convo\.reorder\(",
        r"convo\.send\(",
        r"convo\.ask\(",
        r"convo\.answer\(",
        r"convo\.abandon\(",
        r"set_pointer|preferred_child|delete_subtree|add_user|add_assistant|canonical_leaf",
    ]
    hits = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if re.search(pattern, source):
                hits.append((path.name, pattern))
    assert hits == []
