"""Commands: the only way to write a layer.

A basic command declares exactly one write layer, a read/effect, and an
inverse.  The executor opens that layer for the duration of the effect and
the conversation refuses any layer write whose scope does not match.  A
composite is a sequence of basic commands, each gated and scoped separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Conversation, Layer, Node, Role, Status, new_id, ROOT_SCOPE


@dataclass
class ExecutionResult:
    command: "Command"
    created: list[Node] = field(default_factory=list)


class Command:
    write_layer: str = ROOT_SCOPE
    label: str = ""
    irreversible: bool = False

    def steps(self) -> list["Command"]:
        return [self]

    def apply(self, convo: Conversation) -> Node | None:
        raise NotImplementedError

    @property
    def inverse(self) -> "Command | None":
        return None


class AddChild(Command):
    def __init__(
        self,
        write_layer: str,
        role: Role,
        text: str = "",
        *,
        node_id: str | None = None,
        status: Status = "complete",
        model: str | None = None,
        reasoning: str = "",
        activate: bool = True,
        meta: dict[str, Any] | None = None,
        label: str = "",
    ) -> None:
        self.write_layer = write_layer
        self.role = role
        self.text = text
        self.node_id = node_id or new_id()
        self.status = status
        self.model = model
        self.reasoning = reasoning
        self.activate = activate
        self.meta = dict(meta or {})
        self.label = label or f"add {role}"

    def apply(self, convo: Conversation) -> Node:
        return convo.create(
            self.write_layer,
            self.role,
            self.text,
            node_id=self.node_id,
            status=self.status,
            model=self.model,
            reasoning=self.reasoning,
            activate=self.activate,
            meta=self.meta,
        )

    @property
    def inverse(self) -> "Command":
        return CloseChild(self.write_layer, self.node_id)


class CreateRoot(Command):
    write_layer = ROOT_SCOPE
    irreversible = True

    def __init__(
        self,
        text: str,
        *,
        node_id: str | None = None,
        model: str | None = None,
        label: str = "",
    ) -> None:
        self.text = text
        self.node_id = node_id or new_id()
        self.model = model
        self.label = label or "create root"

    def apply(self, convo: Conversation) -> Node:
        return convo.create(
            None,
            "user",
            self.text,
            node_id=self.node_id,
            status="complete",
            model=self.model,
        )


class SetActive(Command):
    def __init__(
        self,
        write_layer: str,
        child_id: str | None,
        *,
        old_id: str | None = None,
        label: str = "",
    ) -> None:
        self.write_layer = write_layer
        self.child_id = child_id
        self.old_id = old_id
        self.label = label or "switch"

    def apply(self, convo: Conversation) -> None:
        convo.activate(self.write_layer, self.child_id)

    @property
    def inverse(self) -> "Command":
        return SetActive(self.write_layer, self.old_id, old_id=self.child_id)


class CloseChild(Command):
    irreversible = True

    def __init__(self, write_layer: str, child_id: str, *, label: str = "") -> None:
        self.write_layer = write_layer
        self.child_id = child_id
        self.label = label or "close"

    def apply(self, convo: Conversation) -> None:
        convo.close(self.write_layer, self.child_id)


class Reorder(Command):
    def __init__(
        self,
        write_layer: str,
        child_id: str,
        index: int,
        *,
        old_index: int | None = None,
        label: str = "",
    ) -> None:
        self.write_layer = write_layer
        self.child_id = child_id
        self.index = index
        self.old_index = old_index
        self.label = label or "reorder"

    def apply(self, convo: Conversation) -> None:
        convo.reorder(self.write_layer, self.child_id, self.index)

    @property
    def inverse(self) -> "Command | None":
        if self.old_index is None:
            return None
        return Reorder(self.write_layer, self.child_id, self.old_index)


class RepairEmpty(Command):
    irreversible = True

    def __init__(self, write_layer: str, child_id: str, *, label: str = "") -> None:
        self.write_layer = write_layer
        self.child_id = child_id
        self.label = label or "repair empty stream"

    def apply(self, convo: Conversation) -> None:
        convo.abandon(self.child_id)


class Composite(Command):
    def __init__(
        self,
        steps: list[Command],
        *,
        inverse: Command | None = None,
        label: str = "",
        irreversible: bool | None = None,
    ) -> None:
        self._steps = list(steps)
        self._inverse = inverse
        self.label = label or "composite"
        self.irreversible = (inverse is None) if irreversible is None else irreversible

    def steps(self) -> list[Command]:
        out: list[Command] = []
        for step in self._steps:
            out.extend(step.steps())
        return out

    @property
    def inverse(self) -> "Command | None":
        return self._inverse


def execute(convo: Conversation, command: Command) -> ExecutionResult:
    """Run each basic step under its own gate and its own write scope."""
    result = ExecutionResult(command)
    for step in command.steps():
        layer = step.write_layer
        if layer != ROOT_SCOPE:
            convo.layer(layer).require_open()
        with convo._writing(layer):
            node = step.apply(convo)
        if isinstance(node, Node):
            result.created.append(node)
    return result


# -- small builders used by tests, tools and the UI ---------------------
def _parent_id(parent: str | Layer) -> str:
    return parent.parent_id if isinstance(parent, Layer) else parent


def add_child(
    convo: Conversation,
    parent: str | Layer,
    role: Role,
    text: str = "",
    **kwargs: Any,
) -> Node:
    pid = _parent_id(parent)
    cmd = AddChild(pid, role, text, **kwargs)
    execute(convo, cmd)
    return convo.get(cmd.node_id)


def add_root(convo: Conversation, text: str, **kwargs: Any) -> Node:
    cmd = CreateRoot(text, **kwargs)
    execute(convo, cmd)
    return convo.get(cmd.node_id)


def set_active(convo: Conversation, parent: str | Layer, child_id: str | None) -> None:
    pid = _parent_id(parent)
    old = convo.layer(pid).active
    execute(convo, SetActive(pid, child_id, old_id=old.id if old else None))


def close(convo: Conversation, parent: str | Layer, child_id: str) -> int:
    pid = _parent_id(parent)
    before = len(convo.nodes)
    execute(convo, CloseChild(pid, child_id))
    return before - len(convo.nodes)


def reorder(convo: Conversation, parent: str | Layer, child_id: str, index: int) -> None:
    pid = _parent_id(parent)
    old_order = convo.display_ids(pid)
    old_index = old_order.index(child_id) if child_id in old_order else None
    execute(convo, Reorder(pid, child_id, index, old_index=old_index))


def repair_empty(convo: Conversation, child_id: str) -> bool:
    node = convo.get(child_id)
    if node.parent_id is None:
        return False
    execute(convo, RepairEmpty(node.parent_id, child_id))
    return True
