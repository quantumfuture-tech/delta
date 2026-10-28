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


def test_stream_complete_and_persist(qapp, window):
    window.client = FakeClient(["你", "好", "\n\n## 标题"])
    window.composer.set_text("测试")
    window._send("测试")
    assert wait_idle(qapp, window)
    chain = window.convo.chain()
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
    chain = window.convo.chain()
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
    assert window.convo.chain()[-1].text == "ok"
    assert window.composer.text() == ""


def test_final_markdown_after_streaming(qapp, window):
    from delta.ui.markdown_render import DOC_CSS

    window.client = FakeClient(["## 标题", "\n\n**粗体** 和 `代码`"])
    window._send("渲染")
    assert wait_idle(qapp, window)
    node = window.convo.chain()[-1]
    card = window.transcript.card_for(node.id)
    assert card is not None
    assert "标题" in card.body.toPlainText()
    assert card.body.document().defaultStyleSheet() == DOC_CSS
    assert card.body.document().characterCount() > 5


def test_error_without_content_leaves_no_half_node(qapp, window):
    window.client = FakeClient([], error=StreamError("auth", "nope", status=401))
    window._send("拒绝我")
    assert wait_idle(qapp, window)
    chain = window.convo.chain()
    assert len(chain) == 1
    assert window.convo.pointer_id == chain[0].id
    assert all(n.role == "user" for n in window.convo.nodes.values())
    assert window.notice.isVisible()
    assert not window.composer._busy


def test_branch_switch_only_replaces_suffix(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("left"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "LA"
    c.set_pointer(a1.id)
    b1 = c.add_user("right"); b2 = c.add_assistant(b1.id, status="complete"); b2.text = "RA"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)
    before = window.transcript.row_widgets()
    prefix = {k: id(v) for k, v in before.items() if k in ("n:" + u1.id, "n:" + a1.id, "f:" + a1.id)}
    assert "f:" + a1.id in before
    window._switch_branch(a1.id, b1.id)
    pump(qapp, 60)
    after = window.transcript.row_widgets()
    assert all(k in after and id(after[k]) == old for k, old in prefix.items())
    assert [n.id for n in c.chain()] == [u1.id, a1.id, b1.id, b2.id]
    assert u2.id in c.nodes and a2.id in c.nodes
    assert c.nodes[a2.id].text == "LA"


def test_preferred_changes_only_one_node(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete")
    u2 = c.add_user("left")
    a2 = c.add_assistant(u2.id, status="complete")
    c.set_pointer(a1.id)
    b1 = c.add_user("right")
    b2 = c.add_assistant(b1.id, status="complete")
    c.set_pointer(a2.id)
    window.transcript.rebuild(); pump(qapp, 50)
    prefs = {n.id: n.preferred_child for n in c.nodes.values()}
    texts = {n.id: n.text for n in c.nodes.values()}
    pointer = c.pointer_id
    window._toggle_preferred(a1.id, b1.id)
    assert c.nodes[a1.id].preferred_child == b1.id
    assert all(c.nodes[nid].preferred_child == old for nid, old in prefs.items() if nid != a1.id)
    assert c.pointer_id == pointer
    assert all(c.nodes[nid].text == old for nid, old in texts.items())


def test_restart_restores_pointer_and_scroll(qapp, tmp_path):
    store = Store(tmp_path / "restart")
    c = Conversation(title="restart")
    for i in range(14):
        u = c.add_user("第 %d 个问题，带一点长度让内容超过一屏。" % i)
        a = c.add_assistant(u.id, status="complete")
        a.text = ("回答 %d。\n\n" % i) + ("这是一段用于撑高页面的内容。" * 6)
        c.set_pointer(a.id)
    store.save(c)
    w = MainWindow(store, store.read_settings(), c)
    w.resize(1280, 800); w.show()
    pump(qapp, 200)
    w._refresh_stack(); w.transcript.rebuild(); w._refresh_meta(); pump(qapp, 100)
    assert w.transcript.verticalScrollBar().maximum() > 0
    w.transcript.verticalScrollBar().setValue(260)
    pump(qapp, 220)
    state = w.transcript.capture()
    c.ui["scroll"] = state
    w._save_now()
    w.close(); pump(qapp, 50)
    loaded = store.load(c.id)
    assert loaded.ui["scroll"]["key"] == state["key"]
    w2 = MainWindow(store, store.read_settings(), loaded)
    w2.resize(1280, 800); w2.show()
    pump(qapp, 500)
    state2 = w2.transcript.capture()
    for _ in range(3):
        if state2["key"] == state["key"]:
            break
        pump(qapp, 150)
        state2 = w2.transcript.capture()
    assert state2["key"] == state["key"]
    assert abs(state2["offset"] - state["offset"]) <= 2
    assert w2.convo.pointer_id == c.pointer_id
    w2.close(); w2.deleteLater(); pump(qapp, 30)


def test_rapid_operations_do_not_crosstalk(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("left"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "LA"
    c.set_pointer(a1.id)
    b1 = c.add_user("right"); b2 = c.add_assistant(b1.id, status="complete"); b2.text = "RA"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)

    window.client = FakeClient(["x"] * 80, delay=0.02)
    window._send("streaming on left")
    pump(qapp, 120)
    streaming_id = window._active_node_id
    assert streaming_id is not None
    window._send("must be ignored")
    pump(qapp, 40)
    user_count = sum(1 for n in c.nodes.values() if n.role == "user")
    assert user_count == 4  # u1, u2, b1, streaming prompt
    window._switch_branch(a1.id, b1.id)
    pump(qapp, 80)
    assert [n.id for n in c.chain()] == [u1.id, a1.id, b1.id, b2.id]
    partial_before = len(c.nodes[streaming_id].text)
    window._stop_generation()
    window._stop_generation()
    pump(qapp, 60)
    assert c.nodes[streaming_id].status == "interrupted"
    assert c.nodes[b2.id].text == "RA"
    assert len(c.nodes[streaming_id].text) >= partial_before
    assert window._job is None
    # a new send on the visible branch works and does not touch the interrupted node
    window.client = FakeClient(["new", " answer"])
    window._send("after")
    assert wait_idle(qapp, window)
    assert c.nodes[b2.id].text == "RA"
    assert window.convo.chain()[-1].text == "new answer"

def test_switch_back_reuses_cached_cards(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("left"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "LA"
    c.set_pointer(a1.id)
    b1 = c.add_user("right"); b2 = c.add_assistant(b1.id, status="complete"); b2.text = "RA"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 70)
    left_card = window.transcript.card_for(a2.id)
    assert left_card is not None
    window._switch_branch(a1.id, b1.id); pump(qapp, 70)
    assert window.transcript.card_for(a2.id) is None
    window._switch_branch(a1.id, u2.id); pump(qapp, 70)
    assert window.transcript.card_for(a2.id) is left_card
    assert window.convo.chain()[-1].id == a2.id


def test_cached_streaming_card_refreshes_when_returned(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("left"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "LA"
    c.set_pointer(a1.id)
    b1 = c.add_user("right"); b2 = c.add_assistant(b1.id, status="complete"); b2.text = "RA"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 70)
    window.client = FakeClient(["x"] * 40, delay=0.02)
    window._send("live here")
    pump(qapp, 100)
    stream_id = window._active_node_id
    window._switch_branch(a1.id, b1.id); pump(qapp, 80)
    assert window.transcript.card_for(stream_id) is None
    window._stop_generation(); pump(qapp, 80)
    assert c.nodes[stream_id].status == "interrupted"
    window._switch_branch(a1.id, u2.id); pump(qapp, 100)
    card = window.transcript.card_for(stream_id)
    assert card is not None
    assert card.node.status == "interrupted"
    assert card.node.text == c.nodes[stream_id].text

def test_alt_down_cycles_sibling_branches(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("left"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "LA"
    c.set_pointer(a1.id)
    b1 = c.add_user("right"); b2 = c.add_assistant(b1.id, status="complete"); b2.text = "RA"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 100)
    window.composer.focus_input(); pump(qapp, 40)
    QTest.keyClick(window.composer.edit, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    pump(qapp, 150)
    assert window.convo.chain()[-1].id == b2.id
    QTest.keyClick(window.composer.edit, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    pump(qapp, 150)
    assert window.convo.chain()[-1].id == a2.id

def _seed_answer(window, qapp):
    c = window.convo
    u = c.add_user("触发重试的问题")
    a1 = c.add_assistant(u.id, status="complete")
    a1.text = "第一个回答"
    c.set_pointer(a1.id)
    window._refresh_stack()
    window.transcript.rebuild()
    window._refresh_meta()
    pump(qapp, 80)
    return c, u, a1


def test_retry_completion_updates_fork_chip_without_click(qapp, window):
    c, u, a1 = _seed_answer(window, qapp)
    window.client = FakeClient(["第二个回答需要足够长，", "才能让气泡从占位变宽，", "并且不靠点击刷新。"], delay=0.08)
    window._retry_node(a1.id)
    bar = window.transcript._forks.get(u.id)
    assert bar is not None
    before = bar.chip_texts()
    before_width = bar._chips[-1].width()
    assert before[-1] == "（待生成）", before
    assert wait_idle(qapp, window)
    pump(qapp, 80)
    bar = window.transcript._forks.get(u.id)
    assert bar is not None
    after = bar.chip_texts()
    assert not any("待生成" in text for text in after)
    assert "第二个回答" in after[-1]
    assert bar._chips[-1].width() > before_width
    assert bar._chips[-1].display_text == after[-1]


def test_retry_interrupt_updates_fork_chip(qapp, window):
    c, u, a1 = _seed_answer(window, qapp)
    pump(qapp, 80)
    window.client = FakeClient(["半句", "还没有", "结束", "的回答"] * 20, delay=0.02)
    window._retry_node(a1.id)
    pump(qapp, 120)
    window._stop_generation()
    pump(qapp, 80)
    bar = window.transcript._forks.get(u.id)
    assert bar is not None
    texts = bar.chip_texts()
    assert not any("待生成" in text for text in texts)
    partial = c.nodes[window.convo.chain()[-1].id].text
    assert partial
    assert any(partial[:2] in text for text in texts if text != "第一个回答")


def test_retry_401_removes_placeholder(qapp, window):
    c, u, a1 = _seed_answer(window, qapp)
    pump(qapp, 80)
    window.client = FakeClient([], error=StreamError("auth", "bad key", status=401))
    window._retry_node(a1.id)
    assert wait_idle(qapp, window)
    pump(qapp, 80)
    bar = window.transcript._forks.get(u.id)
    if bar is not None:
        assert not any("待生成" in text for text in bar.chip_texts())
    children = c.children(u.id)
    assert [n.id for n in children] == [a1.id]
    assert window.notice.isVisible()

class _DialogStub:
    result = QDialog.DialogCode.Accepted

    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return self.result


def test_delete_confirmation_actually_deletes(qapp, window, monkeypatch):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "A"
    c.set_pointer(a1.id)
    u2 = c.add_user("next"); a2 = c.add_assistant(u2.id, status="complete"); a2.text = "B"
    c.set_pointer(a2.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)
    before_total = len(c.nodes)
    before_children = len(c.children(u2.id))
    _DialogStub.result = QDialog.DialogCode.Accepted
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    window._delete_node(a2.id)
    pump(qapp, 80)
    assert a2.id not in c.nodes
    assert len(c.nodes) == before_total - 1
    assert len(c.children(u2.id)) == before_children - 1
    assert c.pointer_id == u2.id


def test_delete_cancel_keeps_nodes(qapp, window, monkeypatch):
    c = window.convo
    u = c.add_user("root")
    a = c.add_assistant(u.id, status="complete"); a.text = "A"
    c.set_pointer(a.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)
    before = len(c.nodes)
    _DialogStub.result = QDialog.DialogCode.Rejected
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    window._delete_node(a.id)
    pump(qapp, 80)
    assert a.id in c.nodes
    assert len(c.nodes) == before
    assert c.pointer_id == a.id


def test_user_card_has_three_actions_and_no_retry(qapp, window, monkeypatch):
    c = window.convo
    u = c.add_user("用户消息")
    c.set_pointer(u.id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)
    card = window.transcript.card_for(u.id)
    assert card is not None
    card._set_actions_visible(True)
    visible = [b for b in card.action_buttons if b.isVisible()]
    assert len(card.action_buttons) == 3
    assert len(visible) == 3
    assert card.btn_retry is None
    assert all(b is not getattr(card, "btn_retry", None) for b in visible)

    calls = []
    monkeypatch.setattr(window, "_begin_assistant", lambda *a, **k: calls.append(1))
    _DialogStub.result = QDialog.DialogCode.Rejected
    monkeypatch.setattr(main_window_module, "ConfirmDialog", _DialogStub)
    for button in card.action_buttons:
        button.clicked.emit()
        qapp.processEvents()
    assert calls == []
    assert u.id in c.nodes

    c.set_pointer(u.id)
    a = c.add_assistant(u.id, status="complete"); a.text = "A"
    window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 80)
    acard = window.transcript.card_for(a.id)
    acard._set_actions_visible(True)
    assert len(acard.action_buttons) == 4
    assert acard.btn_retry is not None
    assert acard.btn_retry in acard.action_buttons


def _assert_all_chips_inside(bar):
    assert len(bar._chips) == 5
    assert bar._flow_host.height() >= 2 * 33 + 7
    assert bar.height() >= 112
    for chip in bar._chips:
        pos = chip.mapTo(bar, QPoint(0, 0))
        assert chip.isVisible()
        assert pos.y() >= 0
        assert pos.y() + chip.height() <= bar.height()


def test_fork_bar_five_branches_keeps_two_rows(qapp, window):
    c = window.convo
    u1 = c.add_user("root")
    a1 = c.add_assistant(u1.id, status="complete"); a1.text = "parent"
    c.set_pointer(a1.id)
    children = []
    for i in range(5):
        c.set_pointer(a1.id)
        child = c.add_user(("分支 %d：" % (i + 1)) + "这是一段足够长的预览文字，用来把分支气泡撑到最大宽度并迫使他们换行。" * 2)
        children.append(child)
    c.set_pointer(children[2].id)
    window._refresh_stack(); window.transcript.rebuild(); window._refresh_meta(); pump(qapp, 150)

    bar = window.transcript._forks.get(a1.id)
    assert bar is not None
    _assert_all_chips_inside(bar)

    QTest.mouseClick(bar._chips[2], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, bar._chips[2].height() // 2))
    pump(qapp, 120)
    bar = window.transcript._forks.get(a1.id)
    _assert_all_chips_inside(bar)

    window._focus_node(a1.id)
    pump(qapp, 120)
    bar = window.transcript._forks.get(a1.id)
    assert bar is not None
    _assert_all_chips_inside(bar)

    QTest.mouseClick(bar._chips[4], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, bar._chips[4].height() // 2))
    pump(qapp, 120)
    bar = window.transcript._forks.get(a1.id)
    _assert_all_chips_inside(bar)