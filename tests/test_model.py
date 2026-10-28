import pytest

from delta.core.context import chain_messages, context_size
from delta.core.model import Conversation


def snapshot(convo):
    return {
        nid: {
            "text": n.text,
            "role": n.role,
            "parent": n.parent_id,
            "preferred": n.preferred_child,
            "status": n.status,
        }
        for nid, n in convo.nodes.items()
    }


def build():
    """linear chain: u1 -> a1 -> u2 -> a2"""
    c = Conversation(title="root")
    u1 = c.add_user("第一个问题")
    a1 = c.add_assistant(u1.id, status="complete")
    a1.text = "第一个回答"
    u2 = c.add_user("追问")
    a2 = c.add_assistant(u2.id, status="complete")
    a2.text = "第二个回答"
    return c, u1, a1, u2, a2


def test_chain_is_root_to_pointer():
    c, u1, a1, u2, a2 = build()
    assert [n.id for n in c.chain()] == [u1.id, a1.id, u2.id, a2.id]
    c.set_pointer(a1.id)
    assert [n.id for n in c.chain()] == [u1.id, a1.id]
    assert c.pointer_id == a1.id


def test_branch_does_not_touch_existing_branch():
    c, u1, a1, u2, a2 = build()
    before = snapshot(c)
    c.set_pointer(a1.id)
    b1 = c.add_user("换个方向", move=True)
    b2 = c.add_assistant(b1.id, status="complete")
    b2.text = "另一个回答"
    after = snapshot(c)
    for nid in before:
        assert after[nid] == before[nid]
    assert [n.id for n in c.chain()] == [u1.id, a1.id, b1.id, b2.id]
    assert [n.id for n in c.chain(a2.id)] == [u1.id, a1.id, u2.id, a2.id]
    assert len(c.nodes) == 6


def test_pointer_and_preferred_do_not_rewrite_messages():
    c, u1, a1, u2, a2 = build()
    before = snapshot(c)
    c.set_pointer(u1.id)
    c.set_preferred(a1.id, u2.id)
    after = snapshot(c)
    for nid in before:
        assert after[nid]["text"] == before[nid]["text"]
        assert after[nid]["role"] == before[nid]["role"]
        assert after[nid]["parent"] == before[nid]["parent"]
    assert after[a1.id]["preferred"] == u2.id


def test_preferred_child_change_touches_only_that_node():
    c = Conversation()
    u = c.add_user("q")
    a1 = c.add_assistant(u.id, status="complete")
    a2 = c.add_assistant(u.id, status="complete")
    a3 = c.add_assistant(u.id, status="complete")
    c.set_preferred(u.id, a1.id)
    prefs = {nid: n.preferred_child for nid, n in c.nodes.items()}
    c.set_preferred(u.id, a3.id)
    for nid, old in prefs.items():
        if nid == u.id:
            assert c.nodes[nid].preferred_child == a3.id
        else:
            assert c.nodes[nid].preferred_child == old
    assert c.canonical_leaf(u.id) == a3.id


def test_children_are_ordered_and_auto_preferred_once():
    c = Conversation()
    u = c.add_user("q")
    a1 = c.add_assistant(u.id)
    a2 = c.add_assistant(u.id)
    assert [n.id for n in c.children(u.id)] == [a1.id, a2.id]
    assert c.nodes[u.id].preferred_child == a1.id
    c.set_preferred(u.id, a2.id)
    a3 = c.add_assistant(u.id)
    assert c.nodes[u.id].preferred_child == a2.id
    assert len(c.children(u.id)) == 3
    assert c.canonical_leaf(u.id) == a2.id


def test_delete_is_the_only_removal_and_pointer_falls_back():
    c = Conversation()
    u = c.add_user("q")
    a = c.add_assistant(u.id, status="complete")
    a.text = "a"
    c.set_pointer(u.id)
    b = c.add_user("sibling")
    db = c.add_assistant(b.id, status="complete")
    db.text = "b"
    before = snapshot(c)
    removed = c.delete_subtree(b.id)
    assert removed == 2
    assert b.id not in c.nodes and db.id not in c.nodes
    assert c.pointer_id == u.id
    assert c.nodes[u.id].preferred_child == a.id
    assert before[a.id] == snapshot(c)[a.id]


def test_delete_root_on_single_chain_empties_tree():
    c = Conversation()
    u = c.add_user("q")
    c.add_assistant(u.id)
    assert c.delete_subtree(u.id) == 2
    assert c.is_empty() and c.pointer_id is None
    u2 = c.add_user("new root")
    assert c.root_id() == u2.id


def test_invalid_preferred_is_rejected():
    c, u1, a1, u2, a2 = build()
    with pytest.raises(ValueError):
        c.set_preferred(u1.id, a2.id)
    with pytest.raises(KeyError):
        c.set_preferred(u1.id, "nope")


def test_context_is_only_the_visible_chain():
    c, u1, a1, u2, a2 = build()
    c.set_pointer(a1.id)
    b1 = c.add_user("branch B")
    b2 = c.add_assistant(b1.id, status="complete")
    b2.text = "B answer"
    msgs = chain_messages(c)
    assert [m["content"] for m in msgs] == ["第一个问题", "第一个回答", "branch B", "B answer"]
    assert "追问" not in [m["content"] for m in msgs]
    assert context_size(c) == 4


def test_context_skips_empty_assistant_placeholder():
    c = Conversation()
    u = c.add_user("q")
    c.add_assistant(u.id, status="streaming")
    assert chain_messages(c) == [{"role": "user", "content": "q"}]


def test_stream_append_and_finish_is_monotonic():
    c = Conversation()
    u = c.add_user("q")
    a = c.add_assistant(u.id)
    c.append_stream(a.id, "he", "thinking")
    c.append_stream(a.id, "llo", "")
    assert a.text == "hello" and a.reasoning == "thinking"
    c.finish_node(a.id, "complete", finish_reason="stop")
    c.append_stream(a.id, " late")
    assert a.text == "hello"


def test_retry_assistant_creates_sibling_not_rewrite():
    c, u1, a1, u2, a2 = build()
    before = a2.text
    retry = c.retry_assistant(a2.id)
    assert retry.parent_id == a2.parent_id
    assert retry.id != a2.id
    assert a2.text == before
    assert len(c.children(a2.parent_id)) == 2
    assert c.pointer_id == retry.id


def test_stats():
    c, u1, a1, u2, a2 = build()
    c.set_pointer(a1.id)
    c.add_user("B")
    stats = c.stats()
    assert stats["nodes"] == 5
    assert stats["forks"] == 1
    assert stats["depth"] == 3
