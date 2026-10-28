"""ATLAS: what the map encodes (edge roles, default edges, node tags)."""
from __future__ import annotations

from delta.core.model import Conversation
from delta.ui.atlas import (
    PLAIN,
    ROUTE,
    SHORTCUT,
    edge_is_default,
    edge_role,
    edge_sets,
    node_tags,
)
from tests import helpers


def tree() -> tuple[Conversation, dict[str, str]]:
    """root -> a1 -> (u2 | u3); u2 -> a2 -> u5 -> a6.  Two forks, one leaf."""
    c = Conversation()
    u1 = helpers.root(c, "q1")
    a1 = helpers.add(c, u1.id, "assistant", "a1")
    u2 = helpers.add(c, a1.id, "user", "u2")
    u3 = helpers.add(c, a1.id, "user", "u3", activate=False)
    a2 = helpers.add(c, u2.id, "assistant", "a2")
    u5 = helpers.add(c, a2.id, "user", "u5")
    a6 = helpers.add(c, u5.id, "assistant", "a6")
    ids = {"u1": u1.id, "a1": a1.id, "u2": u2.id, "u3": u3.id, "a2": a2.id, "u5": u5.id, "a6": a6.id}
    return c, ids


def test_edge_roles_split_at_the_baseline():
    c, ids = tree()
    c.set_baseline(ids["u1"])
    shortcut, route = edge_sets(c)
    assert shortcut == set()
    assert (ids["u1"], ids["a1"]) in route
    assert edge_role(c, ids["a1"], ids["u2"]) == ROUTE
    assert edge_role(c, ids["a1"], ids["u3"]) == PLAIN

    c.set_baseline(ids["a2"])
    assert edge_role(c, ids["u1"], ids["a1"]) == SHORTCUT
    assert edge_role(c, ids["a1"], ids["u2"]) == SHORTCUT
    assert edge_role(c, ids["a2"], ids["u5"]) == ROUTE
    assert edge_role(c, ids["u5"], ids["a6"]) == ROUTE
    assert edge_role(c, ids["a1"], ids["u3"]) == PLAIN


def test_baseline_at_the_leaf_leaves_no_route():
    c, ids = tree()
    c.set_baseline(ids["a6"])
    assert edge_role(c, ids["u5"], ids["a6"]) == SHORTCUT
    assert edge_role(c, ids["u1"], ids["a1"]) == SHORTCUT
    assert edge_role(c, ids["a2"], ids["u5"]) == SHORTCUT


def test_default_edge_is_one_per_layer():
    c, ids = tree()
    assert edge_is_default(c, ids["a1"], ids["u2"])
    assert not edge_is_default(c, ids["a1"], ids["u3"])
    assert edge_is_default(c, ids["a2"], ids["u5"])
    assert not edge_is_default(c, ids["u1"], ids["a2"])


def test_node_tags_name_what_a_node_is():
    c, ids = tree()
    c.set_baseline(ids["a2"])
    assert node_tags(c, ids["a2"]) == ["基准"]
    assert node_tags(c, ids["a6"]) == ["当前", "路线"]
    assert node_tags(c, ids["u1"]) == ["捷径链"]
    assert node_tags(c, ids["a1"]) == ["捷径链", "分叉"]
    assert node_tags(c, ids["u3"]) == ["分支"]

    c.set_baseline(ids["a6"])
    assert node_tags(c, ids["a6"]) == ["基准", "当前"]
