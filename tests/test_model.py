import pytest

from delta.core import commands, slots
from delta.core.context import chain_messages, context_size
from delta.core.model import Conversation, LayerLocked, ROOT_SCOPE, ScopeError
from tests.helpers import (
    activate,
    add,
    answer,
    ask,
    close,
    draft_for,
    raw_remove,
    reorder,
    root,
    send,
)


def snap(convo):
    return {
        nid: {
            "text": n.text,
            "role": n.role,
            "parent": n.parent_id,
            "default": n.default_child,
            "status": n.status,
        }
        for nid, n in convo.nodes.items()
    }


def build():
    """root -> a1 -> u1 -> b1, with a2 under root and b2 under u1."""
    c = Conversation(title="t")
    u0 = root(c, "根问题")
    a1 = add(c, u0.id, "assistant", "A1")
    a2 = add(c, u0.id, "assistant", "A2")
    u1 = add(c, a1.id, "user", "Q1")
    u2 = add(c, a1.id, "user", "Q2")
    b1 = add(c, u1.id, "assistant", "B1")
    b2 = add(c, u1.id, "assistant", "B2")
    activate(c, u0.id, a1.id)
    activate(c, a1.id, u1.id)
    activate(c, u1.id, b1.id)
    return c, u0, a1, a2, u1, u2, b1, b2


def view_ids(c):
    return [n.id for n in c.view()]


def test_view_is_root_to_leaf_reached_from_baseline():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    assert c.baseline_id == u0.id
    assert view_ids(c) == [u0.id, a1.id, u1.id, b1.id]
    c.set_baseline(a2.id)
    assert view_ids(c) == [u0.id, a2.id]
    c.set_baseline(u1.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b1.id]
    c.set_baseline(a1.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b1.id]


def test_view_is_a_pure_function_of_the_three_things():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    activate(c, u1.id, b2.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    activate(c, u0.id, a2.id)
    assert view_ids(c) == [u0.id, a2.id]
    c.set_baseline(u1.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    assert not hasattr(c, "pointer_id")
    assert "view" not in c.to_dict()


def test_baseline_above_default_route_uses_structure():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u1.id)
    activate(c, u1.id, b2.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    assert c.is_locked(u0.id) and c.is_locked(a1.id) and not c.is_locked(u1.id)
    assert [n.id for n in c.shortcut_chain()] == [u0.id, a1.id, u1.id]


def test_locked_set_derives_from_baseline_only():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    assert c.locked_parent_ids() == set()
    c.set_baseline(u1.id)
    assert c.locked_parent_ids() == {u0.id, a1.id}
    c.set_baseline(a2.id)
    assert c.locked_parent_ids() == {u0.id}
    c.set_baseline(u0.id)
    assert c.locked_parent_ids() == set()


def test_layer_mutations_are_blocked_on_the_shortcut_chain():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u1.id)
    before = snap(c)
    for call in (
        lambda: activate(c, u0.id, a2.id),
        lambda: add(c, u0.id, "assistant", "nope"),
        lambda: close(c, u0.id, a1.id),
        lambda: reorder(c, u0.id, a2.id, 0),
        lambda: activate(c, a1.id, u2.id),
    ):
        with pytest.raises(LayerLocked):
            call()
    assert snap(c) == before
    assert c.baseline_id == u1.id


def test_activate_changes_one_default_only():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u0.id)
    before = snap(c)
    activate(c, u1.id, b2.id)
    assert c.nodes[u1.id].default_child == b2.id
    assert c.baseline_id == u0.id
    for nid, row in before.items():
        if nid == u1.id:
            continue
        assert snap(c)[nid] == row
    assert view_ids(c)[-1] == b2.id


def test_activate_same_child_is_a_noop():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    rev = c.rev
    activate(c, u0.id, a1.id)
    assert c.rev == rev


def test_birth_order_is_append_only_and_display_order_is_independent():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    birth = [n.id for n in c.children(u0.id)]
    assert birth == [a1.id, a2.id]
    before_view = view_ids(c)
    reorder(c, u0.id, a2.id, 0)
    assert [n.id for n in c.children(u0.id)] == birth
    assert c.display_ids(u0.id) == [a2.id, a1.id]
    assert view_ids(c) == before_view
    assert c.nodes[u0.id].default_child == a1.id
    reorder(c, u0.id, a1.id, 0)
    assert c.display_ids(u0.id) == [a1.id, a2.id]


def test_create_appends_at_the_end_of_the_display_order():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    reorder(c, u0.id, a1.id, 1)
    assert c.display_ids(u0.id) == [a2.id, a1.id]
    a3 = add(c, u0.id, "assistant", "A3")
    assert c.display_ids(u0.id) == [a2.id, a1.id, a3.id]
    assert c.nodes[u0.id].default_child == a3.id
    assert view_ids(c)[-1] == a3.id


def test_close_active_picks_the_next_display_neighbour():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    reorder(c, u1.id, b2.id, 0)
    assert c.display_ids(u1.id) == [b2.id, b1.id]
    assert c.nodes[u1.id].default_child == b1.id
    removed = close(c, u1.id, b1.id)
    assert removed == 1
    assert c.nodes[u1.id].default_child == b2.id
    assert view_ids(c)[-1] == b2.id
    assert b1.id not in c.nodes


def test_close_active_with_no_siblings_leaves_the_layer_empty():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    close(c, u1.id, b1.id)
    assert c.nodes[u1.id].default_child == b2.id
    close(c, u1.id, b2.id)
    assert c.nodes[u1.id].default_child is None
    assert view_ids(c) == [u0.id, a1.id, u1.id]


def test_close_inactive_leaves_the_active_child_alone():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    close(c, u1.id, b2.id)
    assert c.nodes[u1.id].default_child == b1.id
    assert view_ids(c)[-1] == b1.id


def test_close_removes_the_whole_subtree_and_keeps_birth_order():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    close(c, u0.id, a1.id)
    assert all(nid not in c.nodes for nid in (a1.id, u1.id, u2.id, b1.id, b2.id))
    assert [n.id for n in c.children(u0.id)] == [a2.id]
    assert c.nodes[u0.id].default_child == a2.id


def test_defensive_delete_of_a_shortcut_ancestor_moves_the_baseline_up():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(b1.id)
    assert c.locked_parent_ids() == {u0.id, a1.id, u1.id}
    removed = raw_remove(c, u1.id)
    assert removed == 3
    assert c.baseline_id == a1.id
    assert u1.id not in c.nodes
    assert c.nodes[a1.id].default_child is None
    assert c.validate() is None


def test_send_appends_at_the_view_end_and_activates_the_new_child():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(a1.id)
    activate(c, u1.id, b2.id)
    user, assistant = send(c, "next")
    assert user.parent_id == b2.id
    assert assistant.parent_id == user.id
    assert c.nodes[b2.id].default_child == user.id
    assert c.nodes[user.id].default_child == assistant.id
    assert view_ids(c)[-1] == assistant.id
    assert c.baseline_id == a1.id


def test_default_slot_at_a_user_leaf_is_a_new_answer():
    c = Conversation()
    leaf = root(c, "hello")
    key = slots.default_key(c)
    assert key == slots.SlotKey(leaf.id, None)
    assert slots.mode_for(c, key) == slots.NEW_ANSWER
    store, draft = draft_for(c, key, slots.NEW_ANSWER, "")
    assert slots.can_send(c, key, draft)
    run = slots.execute_slot(c, key, draft)
    assert run.assistant_id is not None
    assert len(c.children(leaf.id)) == 1
    assert c.view()[-1].role == "assistant"


def test_send_on_empty_conversation_creates_the_root():
    c = Conversation()
    user, assistant = send(c, "first")
    assert user.parent_id is None
    assert assistant.parent_id == user.id
    assert c.baseline_id == user.id
    assert view_ids(c) == [user.id, assistant.id]


def test_ask_creates_a_question_under_an_assistant_layer():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    user, assistant = ask(c, a1.id, "another question")
    assert user.parent_id == a1.id
    assert assistant.parent_id == user.id
    assert c.nodes[a1.id].default_child == user.id


def test_answer_creates_a_sibling_answer_and_activates_it():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    fresh = answer(c, u1.id)
    assert fresh.parent_id == u1.id
    assert c.nodes[u1.id].default_child == fresh.id


def test_direct_layer_writes_need_a_declared_scope():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    with pytest.raises(ScopeError):
        c.create(c.layer(u0.id), "assistant", "nope")
    with pytest.raises(ScopeError):
        c.activate(u1.id, b2.id)
    with pytest.raises(ScopeError):
        c.close(u1.id, b1.id)
    with pytest.raises(ScopeError):
        c.reorder(u1.id, b2.id, 0)


def test_a_command_cannot_write_an_undeclared_layer():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    before = len(c.nodes)

    class Bad(commands.Command):
        write_layer = u0.id

        def apply(self, convo):
            convo.create(a1.id, "assistant", "escaped")

    with pytest.raises(ScopeError):
        commands.execute(c, Bad())
    assert len(c.nodes) == before


def test_command_inverses_are_declared():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    add_cmd = commands.AddChild(u1.id, "assistant", "x")
    inv = add_cmd.inverse
    assert isinstance(inv, commands.CloseChild)
    assert inv.write_layer == u1.id
    assert inv.irreversible is True
    assert add_cmd.irreversible is False
    activate_cmd = commands.SetActive(u1.id, b2.id, old_id=b1.id)
    assert activate_cmd.inverse.child_id == b1.id
    reorder_cmd = commands.Reorder(u1.id, b2.id, 0, old_index=1)
    assert reorder_cmd.inverse.index == 1
    assert commands.CloseChild(u1.id, b1.id).inverse is None


def test_composite_gates_every_layer_before_writing():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u1.id)
    before = len(c.nodes)
    cmd = commands.Composite(
        [
            commands.AddChild(u0.id, "assistant", "blocked"),
            commands.AddChild(a1.id, "user", "also blocked"),
        ]
    )
    with pytest.raises(LayerLocked):
        commands.execute(c, cmd)
    assert len(c.nodes) == before


def test_remove_subtree_is_not_a_public_layer_door():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u1.id)
    with pytest.raises(LayerLocked):
        close(c, u0.id, a1.id)


def test_converge_writes_only_the_shortcut_layers_and_keeps_the_view():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(a1.id)
    activate(c, u1.id, b2.id)
    c.set_baseline(b1.id)
    before_view = view_ids(c)
    before = snap(c)
    changed = c.converge(confirm=True)
    assert changed == 1
    assert c.nodes[u1.id].default_child == b1.id
    assert c.baseline_id == b1.id
    assert view_ids(c) == before_view
    for nid, row in before.items():
        if nid in (u0.id, a1.id, u1.id):
            continue
        assert snap(c)[nid] == row
    c.reset_baseline()
    assert view_ids(c) == before_view
    assert c.locked_parent_ids() == set()


def test_converge_requires_confirmation():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(b1.id)
    with pytest.raises(ValueError):
        c.converge()


def test_converge_is_a_noop_at_the_root():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    assert c.converge(confirm=True) == 0


def test_reset_baseline_only_changes_the_baseline():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(b1.id)
    before = snap(c)
    c.reset_baseline()
    assert c.baseline_id == u0.id
    assert snap(c) == before


def test_set_baseline_rejects_unknown_and_clears_only_on_empty():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    with pytest.raises(KeyError):
        c.set_baseline("nope")
    with pytest.raises(ValueError):
        c.set_baseline(None)


def test_empty_conversation_has_no_baseline_and_no_view():
    c = Conversation()
    assert c.baseline_id is None
    assert c.view() == []
    assert c.view_leaf() is None
    assert c.locked_parent_ids() == set()
    r = root(c, "root")
    assert c.baseline_id == r.id
    assert view_ids(c) == [r.id]


def test_context_is_only_the_view():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    activate(c, u0.id, a2.id)
    a2.text = "A2 text"
    assert [m["content"] for m in chain_messages(c)] == ["根问题", "A2 text"]
    assert context_size(c) == 2
    assert "A1" not in [m["content"] for m in chain_messages(c)]
    assert "Q1" not in [m["content"] for m in chain_messages(c)]


def test_context_skips_empty_assistant_placeholder():
    c = Conversation()
    r = root(c, "q")
    add(c, r.id, "assistant", status="streaming")
    assert chain_messages(c) == [{"role": "user", "content": "q"}]


def test_stream_append_and_finish_is_monotonic():
    c = Conversation()
    user, assistant = send(c, "q")
    c.append_stream(assistant.id, "he", "thinking")
    c.append_stream(assistant.id, "llo", "")
    assert assistant.text == "hello" and assistant.reasoning == "thinking"
    c.finish_node(assistant.id, "complete", finish_reason="stop")
    c.append_stream(assistant.id, " late")
    assert assistant.text == "hello"


def test_stats_depth_is_the_view_length():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    stats = c.stats()
    assert stats["nodes"] == 7
    assert stats["forks"] == 3
    assert stats["depth"] == 4
    c.set_baseline(a2.id)
    assert c.stats()["depth"] == 2


def test_validate_rejects_a_dangling_default():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.nodes[u1.id].default_child = b2.id
    c.nodes[b2.id].parent_id = a1.id
    with pytest.raises(ValueError):
        c.validate()


def test_display_order_normalizes_stale_and_missing_ids():
    c = Conversation.from_dict(
        {
            "schema": 2,
            "id": "c1",
            "title": "t",
            "nodes": [
                {"id": "u", "parent_id": None, "role": "user", "text": "u"},
                {"id": "a", "parent_id": "u", "role": "assistant", "text": "a"},
                {"id": "b", "parent_id": "u", "role": "assistant", "text": "b"},
            ],
            "baseline_id": "u",
            "display_order": {"u": ["b", "ghost"]},
        }
    )
    assert c.display_ids("u") == ["b", "a"]
    assert c.baseline_id == "u"


# -- slots and drafts ---------------------------------------------------
def test_default_and_legacy_slot_mapping():
    c = Conversation()
    assert slots.default_key(c) == slots.SlotKey()
    leaf = root(c, "root")
    assert slots.default_key(c) == slots.SlotKey(leaf.id, None)
    assert slots.mode_for(c, slots.default_key(c)) == slots.NEW_ANSWER

    c2 = Conversation()
    r2 = root(c2, "q")
    a2 = add(c2, r2.id, "assistant", "a")
    c2.ui["draft"] = "old unsent text"
    store = slots.DraftStore.from_ui(c2)
    key = store.active_key(c2)
    assert key == slots.SlotKey(a2.id, None)
    assert store.ensure(c2, key).mode == slots.NEW_QUESTION
    assert store.ensure(c2, key).text == "old unsent text"
    assert store.ensure(c2, key).dirty is True
    assert "draft" not in c2.ui


def test_per_slot_drafts_do_not_overwrite_each_other():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    tail = slots.SlotKey(u1.id, None)
    base = slots.SlotKey(u1.id, b1.id)
    store.open(c, tail, slots.NEW_ANSWER)
    store.open(c, base, slots.EDIT_ANSWER)
    store.set_text(c, tail, "tail text")
    store.set_text(c, base, "edited answer")
    assert store.ensure(c, tail).text == "tail text"
    assert store.ensure(c, base).text == "edited answer"
    assert store.ensure(c, tail).dirty and store.ensure(c, base).dirty
    store.active = tail
    assert store.active_key(c) == tail
    store.active = base
    assert store.active_key(c) == base


def test_reset_returns_to_the_mode_initial_and_clears_dirty():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    key = slots.SlotKey(u1.id, b1.id)
    draft = store.open(c, key, slots.EDIT_ANSWER)
    assert draft.text == "B1" and not draft.dirty
    store.set_text(c, key, "manual answer")
    assert store.ensure(c, key).dirty
    store.reset(c, key)
    assert store.ensure(c, key).text == "B1"
    assert not store.ensure(c, key).dirty


def test_mode_switch_requires_confirmation_and_can_be_undone():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    key = slots.SlotKey(u1.id, b1.id)
    draft = store.open(c, key, slots.REWRITE_ANSWER)
    store.set_text(c, key, "make it shorter")
    with pytest.raises(slots.DirtyDraft):
        store.switch_mode(c, key, slots.EDIT_ANSWER)
    store.switch_mode(c, key, slots.EDIT_ANSWER, confirm=True)
    assert store.ensure(c, key).mode == slots.EDIT_ANSWER
    assert store.ensure(c, key).text == "B1"
    assert not store.ensure(c, key).dirty
    store.undo_switch(c, key)
    assert store.ensure(c, key).mode == slots.REWRITE_ANSWER
    assert store.ensure(c, key).text == "make it shorter"
    assert store.ensure(c, key).dirty


def test_clean_mode_switch_is_seamless():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    key = slots.SlotKey(u1.id, b1.id)
    store.open(c, key, slots.REWRITE_ANSWER)
    store.switch_mode(c, key, slots.EDIT_ANSWER)
    assert store.ensure(c, key).mode == slots.EDIT_ANSWER
    store.undo_switch(c, key)
    assert store.ensure(c, key).mode == slots.REWRITE_ANSWER


def test_can_send_rules():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()

    root_key = slots.SlotKey()
    empty = Conversation()
    rstore = slots.DraftStore()
    rd = rstore.ensure(empty, root_key)
    assert not slots.can_send(empty, root_key, rd)
    rd.text = "first"
    assert slots.can_send(empty, root_key, rd)

    qtail = slots.SlotKey(a1.id, None)
    qd = store.ensure(c, qtail, slots.NEW_QUESTION)
    assert not slots.can_send(c, qtail, qd)
    qd.text = "Q3"
    assert slots.can_send(c, qtail, qd)

    atail = slots.SlotKey(u1.id, None)
    ad = store.ensure(c, atail, slots.NEW_ANSWER)
    assert slots.can_send(c, atail, ad)

    revise = slots.SlotKey(a1.id, u1.id)
    rvd = store.ensure(c, revise, slots.REVISE_QUESTION)
    assert not slots.can_send(c, revise, rvd)
    rvd.text = "Q1 changed"
    assert slots.can_send(c, revise, rvd)
    rvd.text = "Q1"
    assert not slots.can_send(c, revise, rvd)

    edit = slots.SlotKey(u1.id, b1.id)
    ed = store.ensure(c, edit, slots.EDIT_ANSWER)
    assert not slots.can_send(c, edit, ed)
    ed.text = "B1 changed"
    assert slots.can_send(c, edit, ed)
    ed.text = "B1"
    assert not slots.can_send(c, edit, ed)

    rw_store = slots.DraftStore()
    rw = rw_store.ensure(c, edit, slots.REWRITE_ANSWER)
    assert slots.can_send(c, edit, rw)
    rw.text = "feedback"
    assert slots.can_send(c, edit, rw)


def test_new_question_plan_is_a_composite_with_one_inverse():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(a1.id, None)
    store, draft = draft_for(c, key, slots.NEW_QUESTION, "Q3")
    run = slots.execute_slot(c, key, draft)
    assert run.write_layer == a1.id
    assert run.needs_model
    assert run.question_id in c.nodes and run.assistant_id in c.nodes
    user = c.nodes[run.question_id]
    assistant = c.nodes[run.assistant_id]
    assert user.parent_id == a1.id and user.text == "Q3"
    assert assistant.parent_id == user.id and assistant.status == "streaming"
    assert c.nodes[a1.id].default_child == user.id
    assert [n.id for n in c.view()] == [u0.id, a1.id, user.id, assistant.id]
    assert [m["content"] for m in run.messages] == ["根问题", "A1", "Q3"]
    assert run.read == [u0.id, a1.id, user.id]
    assert isinstance(run.command, commands.Composite)
    assert [s.write_layer for s in run.command.steps()] == [a1.id, user.id]
    inv = run.command.inverse
    assert isinstance(inv, commands.CloseChild)
    assert inv.write_layer == a1.id and inv.child_id == user.id


def test_new_answer_plan_is_single_and_reads_the_question_chain():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(u1.id, None)
    store, draft = draft_for(c, key, slots.NEW_ANSWER, "")
    run = slots.execute_slot(c, key, draft)
    assert run.write_layer == u1.id
    assert run.needs_model
    assert run.assistant_id in c.nodes
    assert c.nodes[run.assistant_id].parent_id == u1.id
    assert c.nodes[u1.id].default_child == run.assistant_id
    assert [m["content"] for m in run.messages] == ["根问题", "A1", "Q1"]
    assert isinstance(run.command, commands.AddChild)
    assert isinstance(run.command.inverse, commands.CloseChild)


def test_rewrite_answer_empty_is_plain_regeneration():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(u1.id, b1.id)
    store, draft = draft_for(c, key, slots.REWRITE_ANSWER, "")
    run = slots.execute_slot(c, key, draft)
    assert run.needs_model
    assert [m["content"] for m in run.messages] == ["根问题", "A1", "Q1"]
    assert run.feedback == ""


def test_rewrite_answer_with_feedback_reads_the_base_but_not_its_descendants():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(u1.id, b1.id)
    store, draft = draft_for(c, key, slots.REWRITE_ANSWER, "make it shorter")
    run = slots.execute_slot(c, key, draft)
    assert [m["content"] for m in run.messages] == ["根问题", "A1", "Q1", "B1", "make it shorter"]
    assert run.read == [u0.id, a1.id, u1.id, b1.id]
    assert run.base_id == b1.id
    assert c.nodes[b1.id].text == "B1"
    assert run.assistant_id != b1.id


def test_edit_answer_is_a_single_non_model_write():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(u1.id, b1.id)
    store, draft = draft_for(c, key, slots.EDIT_ANSWER, "A manually edited answer")
    run = slots.execute_slot(c, key, draft)
    assert not run.needs_model
    assert run.messages == []
    node = c.nodes[run.assistant_id]
    assert node.role == "assistant" and node.text == "A manually edited answer"
    assert node.status == "complete"
    assert c.nodes[u1.id].default_child == node.id


def test_revise_question_is_a_composite_and_keeps_the_original():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    key = slots.SlotKey(a1.id, u1.id)
    store, draft = draft_for(c, key, slots.REVISE_QUESTION, "Q1 revised")
    original = c.nodes[u1.id].text
    run = slots.execute_slot(c, key, draft)
    new_q = c.nodes[run.question_id]
    assert new_q.parent_id == a1.id and new_q.text == "Q1 revised"
    assert c.nodes[u1.id].text == original
    assert run.assistant_id != u1.id
    assert [m["content"] for m in run.messages][-1] == "Q1 revised"
    assert isinstance(run.command.inverse, commands.CloseChild)


def test_root_plan_writes_the_root_then_the_answer():
    c = Conversation()
    key = slots.SlotKey()
    store, draft = draft_for(c, key, slots.ROOT, "first")
    run = slots.execute_slot(c, key, draft)
    assert run.write_layer == ROOT_SCOPE
    assert c.root_id() == run.question_id
    assert c.nodes[run.assistant_id].parent_id == run.question_id
    assert run.messages == [{"role": "user", "content": "first"}]
    assert isinstance(run.command, commands.Composite)
    assert run.command.irreversible is True


def test_draft_actions_do_not_write_nodes():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    before = len(c.nodes)
    store = slots.DraftStore()
    key = slots.SlotKey(a1.id, u1.id)
    draft = store.open(c, key, slots.REVISE_QUESTION)
    store.set_text(c, key, "changed")
    store.reset(c, key)
    store.switch_mode(c, key, slots.REVISE_QUESTION)
    assert len(c.nodes) == before
    msgs = slots.propose_question_messages(c, key, "changed")
    assert msgs and msgs[-1]["role"] == "user"
    assert len(c.nodes) == before


def test_draft_store_roundtrip_and_prune():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    key = slots.SlotKey(u1.id, b1.id)
    store.open(c, key, slots.EDIT_ANSWER)
    store.set_text(c, key, "unsent")
    c.ui["slots"] = store.to_ui()
    again = slots.DraftStore.from_ui(c)
    assert again.active_key(c) == key
    assert again.ensure(c, key).text == "unsent"
    assert again.ensure(c, key).dirty

    close(c, u1.id, b1.id)
    again.prune(c)
    assert key not in again.items
    assert again.active_key(c) == slots.default_key(c)


def test_unsent_draft_for_a_deleted_base_is_pruned():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    store = slots.DraftStore()
    key = slots.SlotKey(u1.id, b2.id)
    store.open(c, key, slots.EDIT_ANSWER)
    store.set_text(c, key, "will die")
    close(c, u1.id, b2.id)
    store.prune(c)
    assert key not in store.items
