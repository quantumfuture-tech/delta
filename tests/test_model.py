import pytest

from delta.core.context import chain_messages, context_size
from delta.core.model import Conversation, LayerLocked, SendBlocked


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
    u0 = c.create(None, "user", "根问题")
    a1 = c.create(c.layer(u0.id), "assistant", "A1")
    a2 = c.create(c.layer(u0.id), "assistant", "A2")
    u1 = c.create(c.layer(a1.id), "user", "Q1")
    u2 = c.create(c.layer(a1.id), "user", "Q2")
    b1 = c.create(c.layer(u1.id), "assistant", "B1")
    b2 = c.create(c.layer(u1.id), "assistant", "B2")
    c.activate(u0.id, a1.id)
    c.activate(a1.id, u1.id)
    c.activate(u1.id, b1.id)
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
    before = view_ids(c)
    c.activate(u1.id, b2.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    c.activate(u0.id, a2.id)
    assert view_ids(c) == [u0.id, a2.id]
    c.set_baseline(u1.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    # no stored path exists anywhere on the object
    assert not hasattr(c, "pointer_id")
    assert "view" not in c.to_dict()


def test_baseline_above_default_route_uses_structure():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u1.id)
    c.activate(u1.id, b2.id)
    assert view_ids(c) == [u0.id, a1.id, u1.id, b2.id]
    # the baseline chain is structural: a1 is on it even though u0's default
    # can be changed later, and changing it is blocked by the lock
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
        lambda: c.activate(u0.id, a2.id),
        lambda: c.create(c.layer(u0.id), "assistant", "nope"),
        lambda: c.close(u0.id, a1.id),
        lambda: c.reorder(u0.id, a2.id, 0),
        lambda: c.activate(a1.id, u2.id),
    ):
        with pytest.raises(LayerLocked):
            call()
    assert snap(c) == before
    assert c.baseline_id == u1.id


def test_activate_changes_one_default_only():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(u0.id)
    before = snap(c)
    c.activate(u1.id, b2.id)
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
    c.activate(u0.id, a1.id)
    assert c.rev == rev


def test_birth_order_is_append_only_and_display_order_is_independent():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    birth = [n.id for n in c.children(u0.id)]
    assert birth == [a1.id, a2.id]
    before_view = view_ids(c)
    c.reorder(u0.id, a2.id, 0)
    assert [n.id for n in c.children(u0.id)] == birth
    assert c.display_ids(u0.id) == [a2.id, a1.id]
    assert view_ids(c) == before_view
    assert c.nodes[u0.id].default_child == a1.id
    c.reorder(u0.id, a1.id, 0)
    assert c.display_ids(u0.id) == [a1.id, a2.id]


def test_create_appends_at_the_end_of_the_display_order():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.reorder(u0.id, a1.id, 1)
    assert c.display_ids(u0.id) == [a2.id, a1.id]
    a3 = c.create(c.layer(u0.id), "assistant", "A3")
    assert c.display_ids(u0.id) == [a2.id, a1.id, a3.id]
    assert c.nodes[u0.id].default_child == a3.id
    assert view_ids(c)[-1] == a3.id


def test_close_active_picks_the_next_display_neighbour():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.reorder(u1.id, b2.id, 0)
    assert c.display_ids(u1.id) == [b2.id, b1.id]
    assert c.nodes[u1.id].default_child == b1.id
    removed = c.close(u1.id, b1.id)
    assert removed == 1
    assert c.nodes[u1.id].default_child == b2.id
    assert view_ids(c)[-1] == b2.id
    assert b1.id not in c.nodes


def test_close_active_with_no_siblings_leaves_the_layer_empty():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.close(u1.id, b1.id)
    assert c.nodes[u1.id].default_child == b2.id
    c.close(u1.id, b2.id)
    assert c.nodes[u1.id].default_child is None
    assert view_ids(c) == [u0.id, a1.id, u1.id]


def test_close_inactive_leaves_the_active_child_alone():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.close(u1.id, b2.id)
    assert c.nodes[u1.id].default_child == b1.id
    assert view_ids(c)[-1] == b1.id


def test_close_removes_the_whole_subtree_and_keeps_birth_order():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.close(u0.id, a1.id)
    assert all(nid not in c.nodes for nid in (a1.id, u1.id, u2.id, b1.id, b2.id))
    assert [n.id for n in c.children(u0.id)] == [a2.id]
    assert c.nodes[u0.id].default_child == a2.id


def test_defensive_delete_of_a_shortcut_ancestor_moves_the_baseline_up():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(b1.id)
    assert c.locked_parent_ids() == {u0.id, a1.id, u1.id}
    removed = c._remove_subtree(u1.id)  # not reachable through a layer entrance
    assert removed == 3
    assert c.baseline_id == a1.id
    assert u1.id not in c.nodes
    assert c.nodes[a1.id].default_child is None
    assert c.validate() is None


def test_send_appends_at_the_view_end_and_activates_the_new_child():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(a1.id)
    c.activate(u1.id, b2.id)
    user, assistant = c.send("next")
    assert user.parent_id == b2.id
    assert assistant.parent_id == user.id
    assert c.nodes[b2.id].default_child == user.id
    assert c.nodes[user.id].default_child == assistant.id
    assert view_ids(c)[-1] == assistant.id
    assert c.baseline_id == a1.id


def test_send_refuses_when_the_view_ends_on_a_question():
    c = Conversation()
    root = c.create(None, "user", "hello")
    with pytest.raises(SendBlocked):
        c.send("again")
    assert len(c.nodes) == 1 and c.baseline_id == root.id


def test_send_on_empty_conversation_creates_the_root():
    c = Conversation()
    user, assistant = c.send("first")
    assert user.parent_id is None
    assert assistant.parent_id == user.id
    assert c.baseline_id == user.id
    assert view_ids(c) == [user.id, assistant.id]


def test_ask_creates_a_question_under_an_assistant_layer():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    user, assistant = c.ask(a1.id, "another question")
    assert user.parent_id == a1.id
    assert assistant.parent_id == user.id
    assert c.nodes[a1.id].default_child == user.id
    with pytest.raises(ValueError):
        c.ask(u1.id, "wrong layer")


def test_answer_creates_a_sibling_answer_and_activates_it():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    fresh = c.answer(u1.id)
    assert fresh.parent_id == u1.id
    assert c.nodes[u1.id].default_child == fresh.id
    with pytest.raises(ValueError):
        c.answer(a1.id)


def test_remove_subtree_is_not_a_public_layer_door():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    assert not hasattr(c, "delete_subtree")
    c.set_baseline(u1.id)
    with pytest.raises(LayerLocked):
        c.close(u0.id, a1.id)


def test_converge_writes_only_the_shortcut_layers_and_keeps_the_view():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.set_baseline(a1.id)
    c.activate(u1.id, b2.id)  # L(u1) is below the baseline: allowed
    c.set_baseline(b1.id)
    before_view = view_ids(c)
    before = snap(c)
    changed = c.converge(confirm=True)
    assert changed == 1  # u0->a1 and a1->u1 already match; u1->b2 was different
    assert c.nodes[u1.id].default_child == b1.id
    assert c.baseline_id == b1.id
    assert view_ids(c) == before_view
    assert c.nodes[u0.id].default_child == a1.id
    assert c.nodes[a1.id].default_child == u1.id
    # nothing outside those chain layers moved
    for nid, row in before.items():
        if nid in (u0.id, a1.id, u1.id):
            continue
        assert snap(c)[nid] == row
    # after releasing the baseline, the same place is reached by defaults
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
    root = c.create(None, "user", "root")
    assert c.baseline_id == root.id
    assert view_ids(c) == [root.id]


def test_context_is_only_the_view():
    c, u0, a1, a2, u1, u2, b1, b2 = build()
    c.activate(u0.id, a2.id)
    a2.text = "A2 text"
    assert [m["content"] for m in chain_messages(c)] == ["根问题", "A2 text"]
    assert context_size(c) == 2
    assert "A1" not in [m["content"] for m in chain_messages(c)]
    assert "Q1" not in [m["content"] for m in chain_messages(c)]


def test_context_skips_empty_assistant_placeholder():
    c = Conversation()
    c.create(None, "user", "q")
    root = c.root_id()
    c.create(c.layer(root), "assistant", status="streaming")
    assert chain_messages(c) == [{"role": "user", "content": "q"}]


def test_stream_append_and_finish_is_monotonic():
    c = Conversation()
    user, assistant = c.send("q")
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
