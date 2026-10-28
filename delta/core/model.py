"""The conversation tree: structure, per-layer defaults, and a baseline.

Pure Python.  No Qt, no network, no filesystem.  All semantic guarantees live
here and are exercised by tests/test_model.py.

Vocabulary
----------
node        one message (a user turn or an assistant turn)
layer       the row of children under one parent, including their order;
            the smallest unit an edit can target
default     per layer, the one child a downward walk takes (stored on the
            parent as default_child; at most one, may be empty)
baseline    the node the view starts from; the only remembered position
view        root -> the leaf reached from the baseline by following defaults

The lock is not stored.  A layer is locked iff it lies on the shortcut chain
(root -> baseline), and every operation that changes a layer asks the layer
first.  Locking and unlocking are therefore consequences of set_baseline().
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]
Status = Literal["complete", "streaming", "interrupted", "error"]

SCHEMA = 2
DEFAULT_MODEL = "deepseek-chat"
MODELS = ("deepseek-chat", "deepseek-reasoner")


class LayerLocked(RuntimeError):
    """A layer on the shortcut chain refused a change."""

    def __init__(self, parent_id: str) -> None:
        self.parent_id = parent_id
        super().__init__(f"layer is locked: {parent_id}")


class SendBlocked(RuntimeError):
    """The view ends on a question that has no answer yet."""

    def __init__(self, reason: str = "waiting_for_answer") -> None:
        self.reason = reason
        super().__init__(reason)


def new_id(prefix: str = "n") -> str:
    return prefix + uuid.uuid4().hex[:12]


def _now() -> float:
    return time.time()


def one_line(text: str, limit: int = 96) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def make_title(text: str) -> str:
    flat = one_line(text, 160)
    # Prefer the first sentence; a prompt is usually its own best title.
    cut = len(flat)
    for sep in ("。", "！", "？", "；", "\n", ". ", "! ", "? "):
        idx = flat.find(sep)
        if 0 < idx < cut:
            cut = idx
    if cut < 4:
        cut = min(len(flat), 42)
    title = flat[:cut].strip(" -—·")
    return one_line(title, 42) or "未命名对话"


@dataclass
class Node:
    id: str
    parent_id: str | None
    role: Role
    text: str = ""
    status: Status = "complete"
    model: str | None = None
    reasoning: str = ""
    error_kind: str | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    default_child: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "role": self.role,
            "text": self.text,
            "status": self.status,
            "model": self.model,
            "reasoning": self.reasoning,
            "error_kind": self.error_kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "default_child": self.default_child,
            "meta": dict(self.meta or {}),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Node":
        role = raw.get("role")
        if role not in ("user", "assistant"):
            raise ValueError("node has invalid role")
        status = raw.get("status", "complete")
        if status not in ("complete", "streaming", "interrupted", "error"):
            status = "complete"
        return cls(
            id=str(raw["id"]),
            parent_id=raw.get("parent_id"),
            role=role,
            text=str(raw.get("text", "")),
            status=status,
            model=raw.get("model"),
            reasoning=str(raw.get("reasoning", "")),
            error_kind=raw.get("error_kind"),
            created_at=float(raw.get("created_at", _now())),
            updated_at=float(raw.get("updated_at", _now())),
            default_child=raw.get("default_child", raw.get("preferred_child")),
            meta=dict(raw.get("meta") or {}),
        )


class Layer:
    """One parent's row of children.

    The object is a thin, live handle onto the conversation: it reads the
    current state every time, so a layer cannot go stale when the baseline or
    the display order changes.
    """

    __slots__ = ("_convo", "parent_id")

    def __init__(self, convo: "Conversation", parent_id: str) -> None:
        self._convo = convo
        self.parent_id = parent_id
        convo.get(parent_id)

    @property
    def convo(self) -> "Conversation":
        return self._convo

    @property
    def parent(self) -> Node:
        return self._convo.get(self.parent_id)

    @property
    def children(self) -> list[Node]:
        return self._convo.children(self.parent_id)

    @property
    def count(self) -> int:
        return len(self.children)

    @property
    def tabs(self) -> list[Node]:
        """Children in display order (what the strip shows)."""
        return [self._convo.get(cid) for cid in self._convo.display_ids(self.parent_id)]

    @property
    def active(self) -> Node | None:
        node = self.parent
        if node.default_child is None:
            return None
        child = self._convo.nodes.get(node.default_child)
        if child is None or child.parent_id != node.id:
            return None
        return child

    @property
    def is_locked(self) -> bool:
        return self._convo.is_locked(self.parent_id)

    def require_open(self) -> None:
        if self.is_locked:
            raise LayerLocked(self.parent_id)

    def __len__(self) -> int:
        return self.count

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"Layer({self.parent_id!r}, {self.count} children, locked={self.is_locked})"


class Conversation:
    """A single tree of messages plus a baseline.

    Invariants
    ----------
    * one root (parent_id is None) at most
    * every non-root parent exists
    * default_child (when set) is a direct child
    * display_order is a permutation of each layer's children
    * no cycles
    * baseline_id is None iff the tree is empty, otherwise a node id

    Writers
    -------
    * active child of a layer: activate, create(activate=True), send/ask/answer,
      close (the next display neighbour takes over), converge (global exception)
    * structure: create, close, abandon (an empty streaming placeholder only)
    * display order: reorder
    * baseline: set_baseline in normal operation; from_dict / schema migration
      and the defensive delete-repair path assign it directly

    Nothing except append_stream / finish_node changes a node's text, and
    those are the mechanics of writing a *new* message.  Removal happens only
    through close() or the internal repair helper, and it is always explicit.
    """

    def __init__(
        self,
        *,
        id: str | None = None,
        title: str = "",
        model: str = DEFAULT_MODEL,
        created_at: float | None = None,
    ) -> None:
        self.id = id or new_id("c")
        self.title = title
        self.model = model if model in MODELS else DEFAULT_MODEL
        self.nodes: dict[str, Node] = {}
        self.baseline_id: str | None = None
        self.display_order: dict[str, list[str]] = {}
        self.created_at = created_at if created_at is not None else _now()
        self.updated_at = self.created_at
        self.rev = 0
        self.ui: dict[str, Any] = {}

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def get(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise KeyError(f"unknown node: {node_id}") from None

    # -- tree access ---------------------------------------------------
    def root_id(self) -> str | None:
        for node in self.nodes.values():
            if node.parent_id is None:
                return node.id
        return None

    def parent_of(self, node_id: str) -> Node | None:
        node = self.get(node_id)
        return self.nodes.get(node.parent_id) if node.parent_id else None

    def children(self, node_id: str) -> list[Node]:
        """Children in birth order (the immutable append-only order)."""
        self.get(node_id)
        return [n for n in self.nodes.values() if n.parent_id == node_id]

    def display_ids(self, parent_id: str) -> list[str]:
        """Children ids in display order, normalized against the real layer."""
        self.get(parent_id)
        kids = [n.id for n in self.children(parent_id)]
        kid_set = set(kids)
        order: list[str] = []
        seen: set[str] = set()
        for cid in self.display_order.get(parent_id, []):
            if cid in kid_set and cid not in seen:
                order.append(cid)
                seen.add(cid)
        for cid in kids:
            if cid not in seen:
                order.append(cid)
                seen.add(cid)
        return order

    def sibling_ids(self, node_id: str) -> list[str]:
        node = self.get(node_id)
        if node.parent_id is None:
            return [node_id]
        return [n.id for n in self.children(node.parent_id)]

    def chain(self, node_id: str | None = None, *, include_self: bool = True) -> list[Node]:
        """Root -> node.  With no explicit node, root -> the view leaf."""
        target = self.view_leaf() if node_id is None else node_id
        if target is None:
            return []
        node = self.get(target)
        out = [node] if include_self else []
        seen = {node.id}
        while node.parent_id is not None:
            node = self.get(node.parent_id)
            if node.id in seen:
                raise ValueError("cycle in conversation tree")
            seen.add(node.id)
            out.append(node)
        out.reverse()
        return out

    def descend_defaults(self, node_id: str) -> str:
        """Walk default children from a node until the walk stops."""
        node = self.get(node_id)
        seen: set[str] = {node.id}
        while node.default_child:
            child = self.nodes.get(node.default_child)
            if child is None or child.id in seen:
                break
            node = child
            seen.add(node.id)
        return node.id

    def view_leaf(self) -> str | None:
        if self.baseline_id is None or self.baseline_id not in self.nodes:
            return None
        return self.descend_defaults(self.baseline_id)

    def view(self) -> list[Node]:
        """The visible chain: root -> leaf reached from the baseline."""
        leaf = self.view_leaf()
        return self.chain(leaf) if leaf is not None else []

    def shortcut_chain(self) -> list[Node]:
        """Root -> baseline.  A structural fact, not a choice."""
        if self.baseline_id is None or self.baseline_id not in self.nodes:
            return []
        return self.chain(self.baseline_id)

    def route(self) -> list[Node]:
        """The part of the view strictly below the baseline."""
        return self.view()[len(self.shortcut_chain()):]

    def locked_parent_ids(self) -> set[str]:
        """Layers crossed by the shortcut chain (the lock set, derived)."""
        return {n.parent_id for n in self.shortcut_chain() if n.parent_id is not None}

    def is_locked(self, parent_id: str) -> bool:
        return parent_id in self.locked_parent_ids()

    def is_empty(self) -> bool:
        return not self.nodes

    def layer(self, parent_id: str) -> Layer:
        return Layer(self, parent_id)

    def _resolve_layer(self, layer: str | Layer) -> Layer:
        if isinstance(layer, Layer):
            if layer.convo is not self:
                raise ValueError("layer belongs to another conversation")
            return layer
        return self.layer(layer)

    def stats(self) -> dict[str, int]:
        forks = sum(1 for n in self.nodes.values() if len(self.children(n.id)) > 1)
        leaves = sum(1 for n in self.nodes.values() if not self.children(n.id))
        return {
            "nodes": len(self.nodes),
            "forks": forks,
            "leaves": leaves,
            "depth": len(self.view()),
        }

    def _touch(self) -> None:
        self.updated_at = _now()
        self.rev += 1

    # -- layer mutations (the only doors) ------------------------------
    def _require_child(self, layer: Layer, child_id: str) -> Node:
        child = self.get(child_id)
        if child.parent_id != layer.parent_id:
            raise ValueError("not a child of this layer")
        return child

    def create(
        self,
        parent: str | Layer | None,
        role: Role,
        text: str = "",
        *,
        node_id: str | None = None,
        status: Status = "complete",
        model: str | None = None,
        reasoning: str = "",
        activate: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> Node:
        """Append one child to a layer (or the root when parent is None)."""
        if role not in ("user", "assistant"):
            raise ValueError(f"invalid role: {role}")
        layer: Layer | None = None
        parent_node: Node | None = None
        if parent is None:
            if self.nodes:
                raise ValueError("conversation already has a root")
        else:
            layer = self._resolve_layer(parent)
            layer.require_open()
            parent_node = layer.parent
        if node_id is None:
            node_id = new_id()
        elif node_id in self.nodes:
            raise ValueError(f"duplicate node id: {node_id}")
        node = Node(
            id=node_id,
            parent_id=parent_node.id if parent_node is not None else None,
            role=role,
            text=text,
            status=status,
            model=model,
            reasoning=reasoning,
            meta=dict(meta or {}),
        )
        order: list[str] | None = None
        if layer is not None:
            # Compute the existing display order *before* the new node exists,
            # otherwise the normalizer already includes it and we append twice.
            order = self.display_ids(layer.parent_id)
        self.nodes[node_id] = node
        if layer is not None:
            assert order is not None
            order.append(node_id)
            self.display_order[layer.parent_id] = order
            if activate:
                layer.parent.default_child = node_id
        elif self.baseline_id is None:
            self.baseline_id = node_id
        if role == "user" and not self.title and text.strip():
            self.title = make_title(text)
        self._touch()
        return node

    def activate(self, parent: str | Layer, child_id: str) -> None:
        """Switch one layer: change exactly one default."""
        layer = self._resolve_layer(parent)
        layer.require_open()
        self._require_child(layer, child_id)
        node = layer.parent
        if node.default_child == child_id:
            return
        node.default_child = child_id
        self._touch()

    def close(self, parent: str | Layer, child_id: str) -> int:
        """Close one tab: remove its subtree; the active child picks a neighbour."""
        layer = self._resolve_layer(parent)
        layer.require_open()
        self._require_child(layer, child_id)
        was_active = layer.parent.default_child == child_id
        order = self.display_ids(layer.parent_id)
        neighbor: str | None = None
        if was_active:
            idx = order.index(child_id)
            if idx + 1 < len(order):
                neighbor = order[idx + 1]
            elif idx > 0:
                neighbor = order[idx - 1]
        removed = self._remove_subtree(child_id)
        parent_node = self.nodes.get(layer.parent_id)
        if was_active and parent_node is not None:
            parent_node.default_child = neighbor if neighbor in self.nodes else None
        self._touch()
        return removed

    def reorder(self, parent: str | Layer, child_id: str, index: int) -> None:
        """Move one child within its layer's display order.  Nothing else moves."""
        layer = self._resolve_layer(parent)
        layer.require_open()
        self._require_child(layer, child_id)
        order = self.display_ids(layer.parent_id)
        order.remove(child_id)
        index = max(0, min(int(index), len(order)))
        order.insert(index, child_id)
        if order == self.display_ids(layer.parent_id):
            return
        self.display_order[layer.parent_id] = order
        self._touch()

    def _remove_subtree(self, node_id: str) -> int:
        """Internal repair/delete helper.  Prefer close(), which checks the lock."""
        node = self.get(node_id)
        parent_id = node.parent_id
        doomed: list[str] = []
        stack = [node_id]
        while stack:
            current = stack.pop()
            doomed.append(current)
            stack.extend(n.id for n in self.nodes.values() if n.parent_id == current)
        doomed_set = set(doomed)
        if self.baseline_id in doomed_set:
            ancestor = parent_id
            while ancestor is not None and ancestor in doomed_set:
                ancestor = self.nodes[ancestor].parent_id
            self.baseline_id = ancestor if ancestor in self.nodes else None
        for nid in doomed:
            self.nodes.pop(nid, None)
            self.display_order.pop(nid, None)
        for pid, order in list(self.display_order.items()):
            if pid not in self.nodes:
                del self.display_order[pid]
                continue
            kept = [cid for cid in order if cid in self.nodes and cid not in doomed_set]
            if kept != order:
                self.display_order[pid] = kept
        for parent_node in self.nodes.values():
            if parent_node.default_child is not None:
                child = self.nodes.get(parent_node.default_child)
                if child is None or child.parent_id != parent_node.id:
                    parent_node.default_child = None
        if self.nodes and self.baseline_id is None:
            self.baseline_id = self.root_id()
        return len(doomed)

    # -- baseline (the only position) ----------------------------------
    def set_baseline(self, node_id: str | None) -> None:
        """The only baseline write.  Locks are derived from it, never set."""
        if node_id is None:
            if self.nodes:
                raise ValueError("cannot clear the baseline while nodes exist")
            self.baseline_id = None
            self._touch()
            return
        self.get(node_id)
        if self.baseline_id == node_id:
            return
        self.baseline_id = node_id
        self._touch()

    def reset_baseline(self) -> None:
        self.set_baseline(self.root_id())

    def converge(self, *, confirm: bool = False) -> int:
        """Write the shortcut into defaults: the one global exception to the lock.

        Changes only the default of each layer crossed by root -> baseline.
        The baseline, the structure and every message stay where they are.
        """
        if not confirm:
            raise ValueError("converge requires explicit confirmation")
        chain = self.shortcut_chain()
        if len(chain) < 2:
            return 0
        changed = 0
        for parent_node, child in zip(chain, chain[1:]):
            if parent_node.default_child != child.id:
                parent_node.default_child = child.id
                changed += 1
        if changed:
            self._touch()
        return changed

    # -- writing a new message -----------------------------------------
    def send(self, text: str) -> tuple[Node, Node]:
        """Append at the view end: a user turn plus its streaming answer."""
        leaf = self.view_leaf()
        if leaf is not None and self.nodes[leaf].role == "user":
            raise SendBlocked("waiting_for_answer")
        if leaf is None:
            user = self.create(None, "user", text, model=self.model)
        else:
            user = self.create(self.layer(leaf), "user", text, model=self.model)
        assistant = self.create(
            self.layer(user.id), "assistant", status="streaming", model=self.model
        )
        return user, assistant

    def ask(self, parent_id: str, text: str) -> tuple[Node, Node]:
        """New question under an assistant layer, plus its streaming answer."""
        parent = self.get(parent_id)
        if parent.role != "assistant":
            raise ValueError("a new question attaches under an assistant node")
        user = self.create(self.layer(parent_id), "user", text, model=self.model)
        assistant = self.create(
            self.layer(user.id), "assistant", status="streaming", model=self.model
        )
        return user, assistant

    def answer(self, parent_id: str) -> Node:
        """New answer under a user layer."""
        parent = self.get(parent_id)
        if parent.role != "user":
            raise ValueError("an answer attaches under a user node")
        return self.create(
            self.layer(parent_id), "assistant", status="streaming", model=self.model
        )

    def abandon(self, node_id: str) -> bool:
        """Drop a streaming placeholder that never produced content.

        This is stream repair, not a layer operation: it is only legal while
        the node is still streaming and empty, and it never touches a real
        message.
        """
        node = self.nodes.get(node_id)
        if node is None or node.status != "streaming":
            return False
        if node.text.strip():
            return False
        self._remove_subtree(node_id)
        self._touch()
        return True

    def append_stream(self, node_id: str, content: str = "", reasoning: str = "") -> None:
        node = self.get(node_id)
        if node.status != "streaming":
            return
        if content:
            node.text += content
        if reasoning:
            node.reasoning += reasoning
        if content or reasoning:
            node.updated_at = _now()

    def finish_node(
        self,
        node_id: str,
        status: Status = "complete",
        *,
        error_kind: str | None = None,
        finish_reason: str | None = None,
    ) -> Node:
        node = self.get(node_id)
        node.status = status
        node.error_kind = error_kind
        if finish_reason:
            node.meta["finish_reason"] = finish_reason
        node.updated_at = _now()
        self._touch()
        return node

    # -- conversation metadata ----------------------------------------
    def rename(self, title: str) -> None:
        title = one_line(title, 80)
        if title:
            self.title = title
            self._touch()

    def set_model(self, model: str) -> None:
        if model not in MODELS:
            raise ValueError(model)
        if model != self.model:
            self.model = model
            self._touch()

    def descendant_count(self, node_id: str) -> int:
        count = 0
        stack = [node_id]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                count += 1
                stack.append(child.id)
        return count

    # -- integrity -----------------------------------------------------
    def normalize(self) -> None:
        """Repair harmless inconsistencies after a crash or schema drift.

        Messages are never dropped here.  A message that was mid-stream when
        the process died becomes interrupted with whatever text arrived.
        """
        for node in self.nodes.values():
            if node.status == "streaming":
                node.status = "interrupted"
            if node.parent_id is not None and node.parent_id not in self.nodes:
                root = self.root_id()
                node.parent_id = None if root is None else root
        parents = {n.parent_id for n in self.nodes.values() if n.parent_id is not None}
        cleaned: dict[str, list[str]] = {}
        for pid in parents:
            if pid in self.nodes:
                cleaned[pid] = self.display_ids(pid)
        self.display_order = cleaned
        for node in self.nodes.values():
            if node.default_child is not None:
                child = self.nodes.get(node.default_child)
                if child is None or child.parent_id != node.id:
                    node.default_child = None
        if not self.nodes:
            self.baseline_id = None
        elif self.baseline_id not in self.nodes:
            self.baseline_id = self.root_id()
        self.validate()

    def validate(self) -> None:
        roots = [n for n in self.nodes.values() if n.parent_id is None]
        if len(roots) > 1:
            raise ValueError("more than one root")
        for node in self.nodes.values():
            if node.parent_id is not None and node.parent_id not in self.nodes:
                raise ValueError(f"orphan node {node.id}")
            if node.default_child is not None:
                child = self.nodes.get(node.default_child)
                if child is None:
                    raise ValueError(f"dangling default child on {node.id}")
                if child.parent_id != node.id:
                    raise ValueError(f"default child of {node.id} is not a child")
        for node in self.nodes.values():
            seen_chain: set[str] = set()
            cur: Node | None = node
            while cur is not None:
                if cur.id in seen_chain:
                    raise ValueError("cycle")
                seen_chain.add(cur.id)
                cur = self.nodes.get(cur.parent_id) if cur.parent_id else None
        if self.nodes:
            if self.baseline_id not in self.nodes:
                raise ValueError("baseline does not point at a node")
        elif self.baseline_id is not None:
            raise ValueError("baseline set on an empty conversation")
        for pid, order in self.display_order.items():
            if pid not in self.nodes:
                raise ValueError(f"display order for unknown layer {pid}")
            kids = {n.id for n in self.children(pid)}
            if kids:
                if len(order) != len(kids) or set(order) != kids:
                    raise ValueError(f"display order is not a permutation of {pid}")
            elif order:
                raise ValueError(f"display order for empty layer {pid}")

    # -- persistence ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        display = {
            pid: self.display_ids(pid)
            for pid in self.nodes
            if any(n.parent_id == pid for n in self.nodes.values())
        }
        return {
            "schema": SCHEMA,
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rev": self.rev,
            "baseline_id": self.baseline_id,
            "display_order": display,
            "ui": dict(self.ui),
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Conversation":
        if not isinstance(raw, dict) or "nodes" not in raw:
            raise ValueError("not a conversation document")
        schema = int(raw.get("schema", 1))
        if schema > SCHEMA:
            raise ValueError(f"conversation schema {schema} is newer than this app")
        convo = cls(
            id=str(raw.get("id") or new_id("c")),
            title=str(raw.get("title") or ""),
            model=str(raw.get("model") or DEFAULT_MODEL),
            created_at=float(raw.get("created_at") or _now()),
        )
        for node_raw in raw.get("nodes", []):
            node = Node.from_dict(node_raw)
            if node.id in convo.nodes:
                raise ValueError(f"duplicate node id {node.id}")
            convo.nodes[node.id] = node
        convo.display_order = {
            str(pid): [str(cid) for cid in order]
            for pid, order in (raw.get("display_order") or {}).items()
        }
        convo.baseline_id = raw.get("baseline_id") if schema >= 2 else None
        convo.ui = dict(raw.get("ui") or {})
        convo.rev = int(raw.get("rev", 0))
        convo.updated_at = float(raw.get("updated_at") or convo.created_at)
        if schema <= 1:
            convo._migrate_v1(raw.get("pointer_id"))
        convo.normalize()
        return convo

    def _migrate_v1(self, pointer_id: str | None) -> None:
        """Make the last visible chain the default route, then forget the pointer.

        The first screen after the upgrade is the same chain the user last saw:
        baseline = root, defaults written along root -> pointer.  If the old
        pointer stopped in the middle of a branch, its own default is cleared so
        the view does not grow past the last visible node.
        """
        root = self.root_id()
        if root is None:
            self.baseline_id = None
            return
        known_pointer = pointer_id if pointer_id in self.nodes else None
        target = known_pointer or root
        chain = self.chain(target)
        for parent_node, child in zip(chain, chain[1:]):
            parent_node.default_child = child.id
        if known_pointer is not None and self.children(target):
            self.nodes[target].default_child = None
        self.baseline_id = root

    def summary(self) -> dict[str, Any]:
        leaf_id = self.view_leaf()
        leaf = self.nodes.get(leaf_id) if leaf_id else None
        stats = self.stats()
        return {
            "id": self.id,
            "title": self.title or "未命名对话",
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "nodes": stats["nodes"],
            "forks": stats["forks"],
            "depth": stats["depth"],
            "preview": one_line(leaf.text, 80) if leaf else "",
            "model": self.model,
        }
