"""Schema 1 -> 2: the old pointer becomes a default route, not a hidden position."""
import pytest

from delta.core.model import Conversation, SCHEMA


def _node(nid, parent, role, text, preferred=None):
    return {
        "id": nid,
        "parent_id": parent,
        "role": role,
        "text": text,
        "status": "complete",
        "model": "deepseek-chat",
        "preferred_child": preferred,
    }


def _v1(pointer="b2"):
    return {
        "schema": 1,
        "id": "c-old",
        "title": "old",
        "model": "deepseek-chat",
        "pointer_id": pointer,
        "nodes": [
            _node("u0", None, "user", "root", "a1"),
            _node("a1", "u0", "assistant", "A1", "u1"),
            _node("a2", "u0", "assistant", "A2"),
            _node("u1", "a1", "user", "Q1", "b1"),
            _node("b1", "u1", "assistant", "B1"),
            _node("b2", "u1", "assistant", "B2"),
        ],
    }


def test_old_visible_chain_becomes_the_default_route():
    c = Conversation.from_dict(_v1("b2"))
    assert c.baseline_id == "u0"
    assert [n.id for n in c.view()] == ["u0", "a1", "u1", "b2"]
    assert c.locked_parent_ids() == set()
    assert c.nodes["u0"].default_child == "a1"
    assert c.nodes["a1"].default_child == "u1"
    assert c.nodes["u1"].default_child == "b2"
    assert c.nodes["a2"].default_child is None
    assert c.nodes["b1"].parent_id == "u1"
    assert c.nodes["b2"].parent_id == "u1"
    assert [n.id for n in c.children("u1")] == ["b1", "b2"]
    assert c.display_ids("u0") == ["a1", "a2"]
    assert c.validate() is None


def test_mid_branch_pointer_stops_the_view_where_the_old_pointer_stopped():
    c = Conversation.from_dict(_v1("u1"))
    assert c.baseline_id == "u0"
    assert [n.id for n in c.view()] == ["u0", "a1", "u1"]
    assert c.nodes["u1"].default_child is None
    assert "b1" in c.nodes and "b2" in c.nodes
    assert c.display_ids("u1") == ["b1", "b2"]
    assert c.locked_parent_ids() == set()


def test_missing_pointer_keeps_the_old_default_route():
    c = Conversation.from_dict(_v1(None))
    assert c.baseline_id == "u0"
    assert [n.id for n in c.view()] == ["u0", "a1", "u1", "b1"]


def test_schema_2_roundtrip_preserves_baseline_and_display_order():
    c = Conversation.from_dict(_v1("u1"))
    c.set_baseline("a1")
    c.reorder("u1", "b2", 0)
    again = Conversation.from_dict(c.to_dict())
    assert again.baseline_id == "a1"
    assert again.display_ids("u1") == ["b2", "b1"]
    assert [n.id for n in again.view()] == ["u0", "a1", "u1"]
    assert again.to_dict() == c.to_dict()


def test_future_schema_is_rejected():
    raw = _v1("b2")
    raw["schema"] = SCHEMA + 1
    with pytest.raises(ValueError):
        Conversation.from_dict(raw)


def test_migration_clears_stale_defaults_on_dangling_children():
    raw = _v1("b2")
    raw["nodes"][0]["preferred_child"] = "gone"
    c = Conversation.from_dict(raw)
    assert c.nodes["u0"].default_child == "a1"  # the migrated chain still wins
    assert c.view_leaf() == "b2"
