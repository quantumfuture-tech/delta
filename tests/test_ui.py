import re
import time
from pathlib import Path

import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from delta.core.client import StreamError, StreamResult
from delta.core.model import Conversation
from delta.core.store import Store
import delta.ui.main_window as main_window_module
from delta.ui.main_window import MainWindow


class FakeClient:
    api_key = "sk-test"

    def __init__(self, chunks, delay=0.008, error=None):
        self.chunks = chunks
        self.delay = delay
        self.error = error

    def stream(self, messages, model, cancel=None, on_delta=None):
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


def wait_idle(app, window, timeout=4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if window._job is None:
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


def seed(window, qapp, *, extra_answer=False):
    """root -> a1 -> Q1 -> (B1 | B2); optionally a second answer a2 under root."""
    c = window.convo
    u0 = c.create(None, "user", "root")
    a1 = c.create(c.layer(u0.id), "assistant", "A1")
    a1.text = "A1"
    u1 = c.create(c.layer(a1.id), "user", "Q1")
    b1 = c.create(c.layer(u1.id), "assistant", "B1")
    b1.text = "B1"
    b2 = c.create(c.layer(u1.id), "assistant", "B2")
    b2.text = "B2"
    c.activate(u1.id, b1.id)
    a2 = None
    if extra_answer:
        a2 = c.create(c.layer(u0.id), "assistant", "A2")
        a2.text = "A2"
        c.activate(u0.id, a1.id)
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
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


def test_interrupt_preserves_partial(qapp, window):
    window.client = FakeClient([str(i) for i in range(40)], delay=0.02)
    window._send("long")
    pump(qapp, 180)
    window._stop_generation()
    pump(qapp, 60)
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
    window.composer.set_text("键盘发送")
    QTest.keyClick(window.composer.edit, Qt.Key.Key_Return)
    assert wait_idle(qapp, window)
    assert window.convo.view()[-1].text == "ok"
    assert window.composer.text() == ""


def test_final_markdown_after_streaming(qapp, window):
    from delta.ui.markdown_render import DOC_CSS

    window.client = FakeClient(["## 标题", "\n\n**粗体** 和 `代码`"])
    window._send("渲染")
    assert wait_idle(qapp, window)
    node = window.convo.view()[-1]
    card = window.transcript.card_for(node.id)
    assert card is not None
    assert "标题" in card.body.toPlainText()
    assert card.body.document().defaultStyleSheet() == DOC_CSS


def test_error_without_content_leaves_no_half_node(qapp, window):
    window.client = FakeClient([], error=StreamError("auth", "nope", status=401))
    window._send("拒绝我")
    assert wait_idle(qapp, window)
    chain = window.convo.view()
    assert len(chain) == 1
    assert window.convo.view_leaf() == chain[0].id
    assert all(n.role == "user" for n in window.convo.nodes.values())
    assert window.notice.isVisible()
    assert not window.composer._busy


def test_send_blocked_when_view_ends_on_a_question(qapp, window):
    window.convo.create(None, "user", "only question")
    window.transcript.rebuild()
    window._refresh_meta()
    pump(qapp, 60)
    window.client = FakeClient(["should not run"])
    window._send("more")
    pump(qapp, 40)
    assert len(window.convo.nodes) == 1
    assert window.toast.isVisible()


def test_activate_replaces_only_the_suffix(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
    before = window.transcript.row_widgets()
    prefix = {k: id(v) for k, v in before.items() if k in ("n:" + u0.id, "l:" + u0.id)}
    window._activate(u0.id, a2.id)
    pump(qapp, 60)
    after = window.transcript.row_widgets()
    assert all(k in after and id(after[k]) == old for k, old in prefix.items())
    assert [n.id for n in c.view()] == [u0.id, a2.id]
    assert b1.id in c.nodes and b2.id in c.nodes and u1.id in c.nodes


def test_switch_back_reuses_cached_cards(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
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
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
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
    assert [n.id for n in c.view()][-1] == b2.id


def test_confirm_raises_the_baseline_and_releases_the_layer(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
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
    bar = window.transcript._layers[u0.id]
    chip = next(chip for chip in bar._chips if chip.child.id == a2.id)
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 16))
    pump(qapp, 80)
    assert c.nodes[u0.id].default_child == a2.id
    assert [n.id for n in c.view()] == [u0.id, a2.id]


def test_unlock_confirmation_works_with_tab_and_enter(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
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


def test_new_answer_at_the_answer_layer(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window.client = FakeClient(["fresh answer"])
    window._new_at_layer(u1.id)
    assert wait_idle(qapp, window)
    children = c.children(u1.id)
    assert len(children) == 3
    assert c.nodes[u1.id].default_child == children[-1].id
    assert c.view()[-1].text == "fresh answer"


def test_new_question_at_the_question_layer_uses_the_composer(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._new_at_layer(a1.id)
    assert window._pending_parent_id == a1.id
    window.client = FakeClient(["answer to new question"])
    window._send("Q2")
    assert wait_idle(qapp, window)
    assert window._pending_parent_id is None
    new_q = [n for n in c.children(a1.id) if n.role == "user" and n.text == "Q2"]
    assert len(new_q) == 1
    assert c.nodes[a1.id].default_child == new_q[0].id
    assert c.view()[-1].text == "answer to new question"


def test_reorder_changes_only_the_display_order(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    before_view = [n.id for n in c.view()]
    birth = [n.id for n in c.children(u1.id)]
    window._reorder(u1.id, b2.id, 0)
    pump(qapp, 40)
    assert c.display_ids(u1.id) == [b2.id, b1.id]
    assert [n.id for n in c.children(u1.id)] == birth
    assert [n.id for n in c.view()] == before_view
    assert c.nodes[u1.id].default_child == b1.id


def test_atlas_click_only_changes_the_baseline(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
    defaults = {nid: n.default_child for nid, n in c.nodes.items()}
    texts = {nid: n.text for nid, n in c.nodes.items()}
    window._set_baseline(b2.id)
    pump(qapp, 60)
    assert c.baseline_id == b2.id
    assert c.locked_parent_ids() == {u0.id, a1.id, u1.id}
    assert {nid: n.default_child for nid, n in c.nodes.items()} == defaults
    assert {nid: n.text for nid, n in c.nodes.items()} == texts
    assert [n.id for n in c.view()] == [u0.id, a1.id, u1.id, b2.id]
    window._reset_baseline()
    pump(qapp, 80)
    assert c.baseline_id == u0.id
    assert c.locked_parent_ids() == set()
    assert c.nodes[u0.id].default_child == a1.id


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
    u0 = c.create(None, "user", "root")
    kids = []
    for i in range(8):
        kid = c.create(c.layer(u0.id), "assistant", f"分支 {i}: " + "很长很长的一段预览文字，" * 3)
        kids.append(kid)
    c.activate(u0.id, kids[0].id)
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
    pump(qapp, 150)
    bar = window.transcript._layers[u0.id]
    assert bar is not None
    assert len(bar._chips) == 8
    assert bar.height() == 72
    ys = {chip.mapTo(bar, QPoint(0, 0)).y() for chip in bar._chips}
    assert len(ys) == 1
    assert bar._scroll.horizontalScrollBar().maximum() > 0


def test_restart_restores_baseline_and_scroll(qapp, tmp_path):
    store = Store(tmp_path / "restart")
    c = Conversation(title="restart")
    root = c.create(None, "user", "root")
    last = root
    for i in range(14):
        u = c.create(c.layer(last.id), "user", "第 %d 个问题，带一点长度让内容超过一屏。" % i)
        a = c.create(c.layer(u.id), "assistant", ("回答 %d。\n\n" % i) + ("这是一段用于撑高页面的内容。" * 6))
        last = a
    c.set_baseline(last.parent_id)
    store.save(c)
    w = MainWindow(store, store.read_settings(), c)
    w.resize(1280, 800)
    w.show()
    pump(qapp, 250)
    assert w.transcript.verticalScrollBar().maximum() > 0
    w.transcript.verticalScrollBar().setValue(260)
    pump(qapp, 220)
    state = w.transcript.capture()
    c.ui["scroll"] = state
    w._save_now()
    w.close()
    pump(qapp, 50)
    loaded = store.load(c.id)
    assert loaded.baseline_id == c.baseline_id
    assert loaded.ui["scroll"]["key"] == state["key"]
    w2 = MainWindow(store, store.read_settings(), loaded)
    w2.resize(1280, 800)
    w2.show()
    pump(qapp, 500)
    state2 = w2.transcript.capture()
    for _ in range(3):
        if state2["key"] == state["key"]:
            break
        pump(qapp, 150)
        state2 = w2.transcript.capture()
    assert state2["key"] == state["key"]
    assert abs(state2["offset"] - state["offset"]) <= 2
    assert w2.convo.baseline_id == c.baseline_id
    w2.close()
    w2.deleteLater()
    pump(qapp, 30)


def test_rapid_operations_do_not_crosstalk(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp, extra_answer=True)
    window.client = FakeClient(["x"] * 80, delay=0.02)
    window._send("streaming on the view end")
    pump(qapp, 120)
    streaming_id = window._active_node_id
    assert streaming_id is not None
    window._send("must be ignored")
    pump(qapp, 30)
    user_count = sum(1 for n in c.nodes.values() if n.role == "user")
    assert user_count == 3
    window._activate(u0.id, a2.id)
    pump(qapp, 80)
    assert [n.id for n in c.view()] == [u0.id, a2.id]
    partial_before = len(c.nodes[streaming_id].text)
    window._stop_generation()
    window._stop_generation()
    pump(qapp, 60)
    assert c.nodes[streaming_id].status == "interrupted"
    assert c.nodes[a2.id].text == "A2"
    assert len(c.nodes[streaming_id].text) >= partial_before
    assert window._job is None
    window.client = FakeClient(["new", " answer"])
    window._send("after")
    assert wait_idle(qapp, window)
    assert c.nodes[a2.id].text == "A2"
    assert window.convo.view()[-1].text == "new answer"


def test_converge_writes_the_shortcut_from_the_menu(qapp, window, monkeypatch):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    window._set_baseline(b2.id)
    pump(qapp, 40)
    assert c.nodes[u1.id].default_child == b1.id
    before_view = [n.id for n in c.view()]
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    _DialogStub.result = QDialog.DialogCode.Accepted
    window._converge()
    pump(qapp, 40)
    assert c.nodes[u1.id].default_child == b2.id
    assert c.baseline_id == b2.id
    assert [n.id for n in c.view()] == before_view
    window._reset_baseline()
    pump(qapp, 60)
    assert [n.id for n in c.view()] == before_view


def test_drag_reorders_the_layer_without_moving_the_view(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    target = next(ch for ch in bar._chips if ch.child.id == b1.id)
    pos = target.mapToGlobal(QPoint(2, target.height() // 2))
    bar._on_drag(b2.id, pos)
    pump(qapp, 60)
    assert c.display_ids(u1.id) == [b2.id, b1.id]
    assert c.nodes[u1.id].default_child == b1.id
    assert c.view()[-1].id == b1.id


def test_add_chip_creates_an_answer(qapp, window):
    c, u0, a1, u1, b1, b2, a2 = seed(window, qapp)
    bar = window.transcript._layers[u1.id]
    window.client = FakeClient(["plus answer"])
    QTest.mouseClick(bar._add, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 16))
    assert wait_idle(qapp, window)
    assert c.children(u1.id)[-1].text == "plus answer"
    assert c.view()[-1].text == "plus answer"


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


def test_context_menu_is_the_entrance_for_a_hidden_single_child_layer(qapp, window, monkeypatch):
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
    assert "关闭这一支…" in actions
    window._set_baseline(b2.id)
    pump(qapp, 40)
    window._card_menu(a1.id, QPoint(0, 0))
    assert "这一层已锁 · 抬基准…" in MenuStub.last.actions


def test_ui_has_no_direct_model_writes():
    root = Path(__file__).resolve().parents[1] / "delta" / "ui"
    forbidden = [
        r"\.default_child\s*=(?!=)",
        r"\.baseline_id\s*=(?!=)",
        r"\.display_order\s*=(?!=)",
        r"\.nodes\s*\[[^\]]*\]\s*=(?!=)",
        r"\.nodes\.(pop|clear)\(",
        r"_remove_subtree\(",
        r"_touch\(",
        r"set_pointer|preferred_child|delete_subtree|add_user|add_assistant|canonical_leaf",
    ]
    hits = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if re.search(pattern, source):
                hits.append((path.name, pattern))
    assert hits == []
