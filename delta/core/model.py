"""The conversation tree.

Pure Python. No Qt, no network, no filesystem.  All semantic guarantees in
BRIEF.md live here and are exercised by tests/test_model.py.

Vocabulary
----------
node        one message (a user turn or an assistant turn)
child       a possible continuation of a node; siblings are alternatives
preferred   per node, the one child a "continue downward" walk takes
pointer     the current position; the visible chain is root -> pointer
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]
Status = Literal["complete", "streaming", "interrupted", "error"]

SCHEMA = 1
DEFAULT_MODEL = "deepseek-chat"
MODELS = ("deepseek-chat", "deepseek-reasoner")


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
    preferred_child: str | None = None
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
            "preferred_child": self.preferred_child,
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
            preferred_child=raw.get("preferred_child"),
            meta=dict(raw.get("meta") or {}),
        )


class Conversation:
    """A single tree of messages plus the pointer into it.

    Invariants
    ----------
    * one root (parent_id is None) at most
    * every non-root parent exists
    * preferred_child (when set) is a direct child
    * no cycles
    * pointer_id is None iff the tree is empty, otherwise a node id

    Nothing except append_stream / finish_node changes a node's text, and
    those are the mechanics of writing a *new* message.  Deleting a node is
    the only operation that removes messages, and it is always explicit.
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
        self.pointer_id: str | None = None
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

    def root_id(self) -> str | None:
        for node in self.nodes.values():
            if node.parent_id is None:
                return node.id
        return None

    def parent_of(self, node_id: str) -> Node | None:
        node = self.get(node_id)
        return self.nodes.get(node.parent_id) if node.parent_id else None

    def children(self, node_id: str) -> list[Node]:
        self.get(node_id)
        return [n for n in self.nodes.values() if n.parent_id == node_id]

    def sibling_ids(self, node_id: str) -> list[str]:
        node = self.get(node_id)
        if node.parent_id is None:
            return [node_id]
        return [n.id for n in self.children(node.parent_id)]

    def chain(self, node_id: str | None = None, *, include_self: bool = True) -> list[Node]:
        """Root -> node.  This *is* the visible conversation; no path is stored."""
        target = self.pointer_id if node_id is None else node_id
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

    def pointer(self) -> Node | None:
        return self.nodes.get(self.pointer_id) if self.pointer_id else None

    def is_empty(self) -> bool:
        return not self.nodes

    def children_of_pointer(self) -> list[Node]:
        if self.pointer_id is None:
            return []
        return self.children(self.pointer_id)

    def canonical_leaf(self, node_id: str) -> str:
        """Walk preferred children until a node with no preferred child."""
        node = self.get(node_id)
        seen: set[str] = {node.id}
        while node.preferred_child:
            child = self.nodes.get(node.preferred_child)
            if child is None or child.id in seen:
                break
            node = child
            seen.add(node.id)
        return node.id

    def stats(self) -> dict[str, int]:
        forks = sum(1 for n in self.nodes.values() if len(self.children(n.id)) > 1)
        leaves = sum(1 for n in self.nodes.values() if not self.children(n.id))
        return {
            "nodes": len(self.nodes),
            "forks": forks,
            "leaves": leaves,
            "depth": len(self.chain()),
        }

    def _touch(self) -> None:
        self.updated_at = _now()
        self.rev += 1

    def add_node(
        self,
        parent_id: str | None,
        role: Role,
        text: str = "",
        *,
        node_id: str | None = None,
        status: Status = "complete",
        model: str | None = None,
        reasoning: str = "",
        move: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> Node:
        if role not in ("user", "assistant"):
            raise ValueError(f"invalid role: {role}")
        parent = None
        if parent_id is None:
            if self.nodes:
                raise ValueError("conversation already has a root")
        else:
            parent = self.get(parent_id)
        if node_id is None:
            node_id = new_id()
        elif node_id in self.nodes:
            raise ValueError(f"duplicate node id: {node_id}")
        node = Node(
            id=node_id,
            parent_id=parent_id,
            role=role,
            text=text,
            status=status,
            model=model,
            reasoning=reasoning,
            meta=dict(meta or {}),
        )
        self.nodes[node_id] = node
        if parent is not None and parent.preferred_child is None:
            parent.preferred_child = node_id
        if role == "user" and not self.title and text.strip():
            self.title = make_title(text)
        if move or self.pointer_id is None:
            self.pointer_id = node_id
        self._touch()
        return node

    def add_user(self, text: str, *, model: str | None = None, move: bool = True) -> Node:
        return self.add_node(self.pointer_id, "user", text, model=model, move=move)

    def add_assistant(
        self,
        parent_id: str,
        *,
        model: str | None = None,
        status: Status = "streaming",
        text: str = "",
        reasoning: str = "",
        move: bool = True,
    ) -> Node:
        return self.add_node(
            parent_id, "assistant", text, model=model, status=status,
            reasoning=reasoning, move=move
        )

    def retry_assistant(self, node_id: str, *, model: str | None = None) -> Node:
        """Create a *sibling* of an assistant node, never overwriting it."""
        node = self.get(node_id)
        if node.role != "assistant" or node.parent_id is None:
            raise ValueError("can only retry an assistant node with a parent")
        return self.add_assistant(node.parent_id, model=model or node.model, move=True)

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

    def set_pointer(self, node_id: str | None) -> None:
        if node_id is None:
            if self.nodes:
                raise ValueError("cannot clear pointer while nodes exist")
            self.pointer_id = None
        else:
            self.get(node_id)
            self.pointer_id = node_id
        self._touch()

    def set_preferred(self, parent_id: str, child_id: str | None) -> None:
        """Change *only* this node's preferred child.  No other field moves."""
        parent = self.get(parent_id)
        if child_id is not None:
            child = self.get(child_id)
            if child.parent_id != parent_id:
                raise ValueError("preferred child must be a direct child")
        parent.preferred_child = child_id
        self._touch()

    def toggle_preferred(self, parent_id: str, child_id: str) -> bool:
        parent = self.get(parent_id)
        new = None if parent.preferred_child == child_id else child_id
        self.set_preferred(parent_id, new)
        return new == child_id

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

    def delete_subtree(self, node_id: str) -> int:
        """Explicitly remove a node and every descendant.  Returns count removed."""
        node = self.get(node_id)
        parent_id = node.parent_id
        doomed: list[str] = []
        stack = [node_id]
        while stack:
            current = stack.pop()
            doomed.append(current)
            stack.extend(n.id for n in self.nodes.values() if n.parent_id == current)
        doomed_set = set(doomed)
        for nid in doomed:
            self.nodes.pop(nid, None)
        if parent_id is not None and parent_id in self.nodes:
            parent = self.nodes[parent_id]
            if parent.preferred_child in doomed_set:
                siblings = [n.id for n in self.children(parent_id)]
                parent.preferred_child = siblings[0] if siblings else None
        if self.pointer_id in doomed_set:
            self.pointer_id = parent_id if parent_id in self.nodes else self.root_id()
        if not self.nodes:
            self.pointer_id = None
        self._touch()
        return len(doomed)

    def descendant_count(self, node_id: str) -> int:
        count = 0
        stack = [node_id]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                count += 1
                stack.append(child.id)
        return count

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
        for node in self.nodes.values():
            if node.preferred_child is not None:
                child = self.nodes.get(node.preferred_child)
                if child is None or child.parent_id != node.id:
                    kids = [n.id for n in self.children(node.id)]
                    node.preferred_child = kids[0] if kids else None
        if not self.nodes:
            self.pointer_id = None
        elif self.pointer_id not in self.nodes:
            self.pointer_id = self.root_id()
        self.validate()

    def validate(self) -> None:
        roots = [n for n in self.nodes.values() if n.parent_id is None]
        if len(roots) > 1:
            raise ValueError("more than one root")
        for node in self.nodes.values():
            if node.parent_id is not None and node.parent_id not in self.nodes:
                raise ValueError(f"orphan node {node.id}")
            if node.preferred_child is not None:
                child = self.nodes.get(node.preferred_child)
                if child is None:
                    raise ValueError(f"dangling preferred child on {node.id}")
                if child.parent_id != node.id:
                    raise ValueError(f"preferred child of {node.id} is not a child")
        for node in self.nodes.values():
            seen_chain: set[str] = set()
            cur: Node | None = node
            while cur is not None:
                if cur.id in seen_chain:
                    raise ValueError("cycle")
                seen_chain.add(cur.id)
                cur = self.nodes.get(cur.parent_id) if cur.parent_id else None
        if self.nodes:
            if self.pointer_id not in self.nodes:
                raise ValueError("pointer does not point at a node")
        elif self.pointer_id is not None:
            raise ValueError("pointer set on an empty conversation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rev": self.rev,
            "pointer_id": self.pointer_id,
            "ui": dict(self.ui),
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Conversation":
        if not isinstance(raw, dict) or "nodes" not in raw:
            raise ValueError("not a conversation document")
        schema = raw.get("schema", SCHEMA)
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
        convo.pointer_id = raw.get("pointer_id")
        convo.ui = dict(raw.get("ui") or {})
        convo.rev = int(raw.get("rev", 0))
        convo.updated_at = float(raw.get("updated_at") or convo.created_at)
        convo.normalize()
        return convo

    def summary(self) -> dict[str, Any]:
        pointer = self.pointer()
        stats = self.stats()
        return {
            "id": self.id,
            "title": self.title or "未命名对话",
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "nodes": stats["nodes"],
            "forks": stats["forks"],
            "depth": stats["depth"],
            "preview": one_line(pointer.text, 80) if pointer else "",
            "model": self.model,
        }