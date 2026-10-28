"""Slots and drafts: the intent layer in front of commands.

A slot is a position on a layer: the tail slot (add a new alternative) or the
slot attached to one existing alternative.  Each slot holds at most one draft:
mode + text + dirty.  The active slot is the one bound to the input box.
Sending derives a Command from the active draft; a draft action (reset, mode
switch, model proposal) writes no layer and therefore never touches the lock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import commands
from .context import chain_messages
from .model import Conversation, Node, ROOT_SCOPE, new_id, one_line

# modes -----------------------------------------------------------------
ROOT = "root"
NEW_QUESTION = "new_question"
NEW_ANSWER = "new_answer"
REVISE_QUESTION = "revise_question"
REWRITE_ANSWER = "rewrite_answer"
EDIT_ANSWER = "edit_answer"

MODE_LABEL = {
    ROOT: "首条提问",
    NEW_QUESTION: "新提问",
    NEW_ANSWER: "再生成",
    REVISE_QUESTION: "改问",
    REWRITE_ANSWER: "按意见改",
    EDIT_ANSWER: "自己改",
}

MODE_VERB = {
    ROOT: "发送并生成",
    NEW_QUESTION: "发送并生成",
    NEW_ANSWER: "重新生成",
    REVISE_QUESTION: "改问并生成",
    REWRITE_ANSWER: "按意见改写",
    EDIT_ANSWER: "写入回答",
}

MODE_PROMPT = {
    ROOT: "写下第一句话…",
    NEW_QUESTION: "写下新问题…",
    NEW_ANSWER: "直接生成；也可以写一句补充要求",
    REVISE_QUESTION: "改写这条提问…",
    REWRITE_ANSWER: "写下修改意见；留空 = 普通再生成",
    EDIT_ANSWER: "直接编辑这条回答…",
}


class SlotError(RuntimeError):
    pass


class DirtyDraft(SlotError):
    pass


class CannotSend(SlotError):
    pass


@dataclass(frozen=True)
class SlotKey:
    parent_id: str | None = None
    base_id: str | None = None

    @property
    def is_root(self) -> bool:
        return self.parent_id is None and self.base_id is None

    def to_json(self) -> dict[str, str | None]:
        return {"parent": self.parent_id, "base": self.base_id}

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "SlotKey | None":
        if not isinstance(raw, dict):
            return None
        parent = raw.get("parent")
        base = raw.get("base")
        if parent is None and base is None:
            return cls()
        if not isinstance(parent, str):
            return None
        return cls(parent, base if isinstance(base, str) else None)


def default_key(convo: Conversation) -> SlotKey:
    if convo.is_empty():
        return SlotKey()
    leaf = convo.view_leaf()
    if leaf is None:
        return SlotKey()
    return SlotKey(leaf, None)


def legacy_key(convo: Conversation) -> SlotKey:
    """Where the old single ui.draft goes: the layer whose tail writes a user turn."""
    if convo.is_empty():
        return SlotKey()
    leaf = convo.view_leaf()
    if leaf is None:
        return SlotKey()
    node = convo.nodes[leaf]
    if node.role == "assistant":
        return SlotKey(leaf, None)
    if node.parent_id is not None:
        parent = convo.nodes.get(node.parent_id)
        if parent is not None and parent.role == "assistant":
            return SlotKey(node.parent_id, None)
    return SlotKey(leaf, None)


def slot_valid(convo: Conversation, key: SlotKey) -> bool:
    if key.is_root:
        return convo.is_empty()
    if key.parent_id not in convo.nodes:
        return False
    if key.base_id is None:
        return True
    base = convo.nodes.get(key.base_id)
    return base is not None and base.parent_id == key.parent_id


def mode_for(convo: Conversation, key: SlotKey) -> str:
    if key.is_root:
        return ROOT
    parent = convo.nodes[key.parent_id]
    if key.base_id is not None:
        base = convo.nodes[key.base_id]
        return REVISE_QUESTION if base.role == "user" else REWRITE_ANSWER
    return NEW_QUESTION if parent.role == "assistant" else NEW_ANSWER


def allowed_modes(convo: Conversation, key: SlotKey) -> list[str]:
    if key.is_root:
        return [ROOT]
    if key.base_id is not None:
        base = convo.nodes[key.base_id]
        return [REVISE_QUESTION] if base.role == "user" else [REWRITE_ANSWER, EDIT_ANSWER]
    parent = convo.nodes[key.parent_id]
    return [NEW_QUESTION] if parent.role == "assistant" else [NEW_ANSWER]


def initial_text(convo: Conversation, key: SlotKey, mode: str) -> str:
    if mode in (REVISE_QUESTION, EDIT_ANSWER) and key.base_id is not None:
        return convo.nodes[key.base_id].text
    return ""


def layer_label(convo: Conversation, key: SlotKey) -> str:
    if key.is_root:
        return "新对话"
    parent = convo.nodes.get(key.parent_id)
    return one_line(parent.text, 18) if parent is not None and parent.text.strip() else "对话开头"


def base_label(convo: Conversation, key: SlotKey) -> str:
    if key.base_id is None:
        return "末尾"
    base = convo.nodes.get(key.base_id)
    return one_line(base.text, 18) if base is not None and base.text.strip() else "（空）"


def write_layer(convo: Conversation, key: SlotKey) -> str:
    return ROOT_SCOPE if key.is_root else key.parent_id


def can_send(convo: Conversation, key: SlotKey, draft: "Draft") -> bool:
    if not slot_valid(convo, key):
        return False
    if draft.mode not in allowed_modes(convo, key):
        return False
    text = draft.text
    if draft.mode == REVISE_QUESTION:
        if not text.strip():
            return False
        base = convo.nodes.get(key.base_id) if key.base_id else None
        return base is not None and text != base.text
    if draft.mode in (ROOT, NEW_QUESTION):
        return bool(text.strip())
    if draft.mode == EDIT_ANSWER:
        if not text.strip():
            return False
        base = convo.nodes.get(key.base_id) if key.base_id else None
        return base is not None and text != base.text
    if draft.mode == NEW_ANSWER:
        return True
    if draft.mode == REWRITE_ANSWER:
        return True
    return False


def send_disabled_reason(convo: Conversation, key: SlotKey, draft: "Draft") -> str:
    if not slot_valid(convo, key):
        return "这个槽已经失效"
    if draft.mode in (ROOT, NEW_QUESTION, REVISE_QUESTION, EDIT_ANSWER) and not draft.text.strip():
        return "写点什么"
    if draft.mode == EDIT_ANSWER and key.base_id:
        base = convo.nodes.get(key.base_id)
        if base is not None and draft.text == base.text:
            return "和底稿一样，没有可写入的修改"
    if draft.mode == REVISE_QUESTION and key.base_id:
        base = convo.nodes.get(key.base_id)
        if base is not None and draft.text == base.text:
            return "和底稿一样，没有可写入的修改"
    return ""


@dataclass
class Draft:
    mode: str
    text: str = ""
    dirty: bool = False
    snapshot: tuple[str, str, bool] | None = None


class DraftStore:
    VERSION = 1

    def __init__(self) -> None:
        self.items: dict[SlotKey, Draft] = {}
        self.active: SlotKey | None = None

    # -- persistence ---------------------------------------------------
    @classmethod
    def from_ui(cls, convo: Conversation) -> "DraftStore":
        store = cls()
        raw = convo.ui.get("slots")
        try:
            version = int(raw.get("version", 0)) if isinstance(raw, dict) else 0
        except (TypeError, ValueError):
            version = 0
        if version == cls.VERSION:
            for item in (raw.get("items") or []):
                if not isinstance(item, dict):
                    continue
                key = SlotKey.from_json(item.get("key"))
                if key is None or not slot_valid(convo, key):
                    continue
                mode = item.get("mode")
                if mode not in allowed_modes(convo, key):
                    continue
                store.items[key] = Draft(
                    mode=mode,
                    text=str(item.get("text", "")),
                    dirty=bool(item.get("dirty", False)),
                )
            active = SlotKey.from_json(raw.get("active"))
            if active is not None and slot_valid(convo, active):
                store.active = active
        legacy = convo.ui.pop("draft", "")
        if isinstance(legacy, str) and legacy.strip():
            key = legacy_key(convo)
            mode = mode_for(convo, key)
            store.items[key] = Draft(mode=mode, text=legacy, dirty=True)
            store.active = key
        if store.active is None:
            store.active = default_key(convo)
        store.ensure(convo, store.active)
        return store

    def to_ui(self) -> dict[str, Any]:
        items = []
        for key, draft in self.items.items():
            items.append(
                {
                    "key": key.to_json(),
                    "mode": draft.mode,
                    "text": draft.text,
                    "dirty": draft.dirty,
                }
            )
        return {
            "version": self.VERSION,
            "active": self.active.to_json() if self.active is not None else None,
            "items": items,
        }

    # -- basics --------------------------------------------------------
    def active_key(self, convo: Conversation) -> SlotKey:
        if self.active is not None and slot_valid(convo, self.active):
            return self.active
        self.active = default_key(convo)
        return self.active

    def ensure(self, convo: Conversation, key: SlotKey, mode: str | None = None) -> Draft:
        if not slot_valid(convo, key):
            raise SlotError("slot does not belong to this tree")
        draft = self.items.get(key)
        if draft is None:
            chosen = mode if mode in allowed_modes(convo, key) else mode_for(convo, key)
            draft = Draft(mode=chosen, text=initial_text(convo, key, chosen), dirty=False)
            self.items[key] = draft
        return draft

    def draft(self, convo: Conversation, key: SlotKey) -> Draft:
        return self.ensure(convo, key)

    def open(self, convo: Conversation, key: SlotKey, mode: str | None = None) -> Draft:
        draft = self.ensure(convo, key, mode)
        self.active = key
        return draft

    def set_text(self, convo: Conversation, key: SlotKey, text: str) -> Draft:
        draft = self.ensure(convo, key)
        if text != draft.text:
            draft.text = text
            draft.dirty = True
            draft.snapshot = None
        return draft

    def reset(self, convo: Conversation, key: SlotKey) -> Draft:
        draft = self.ensure(convo, key)
        draft.text = initial_text(convo, key, draft.mode)
        draft.dirty = False
        draft.snapshot = None
        return draft

    def switch_mode(
        self,
        convo: Conversation,
        key: SlotKey,
        mode: str,
        *,
        confirm: bool = False,
    ) -> Draft:
        draft = self.ensure(convo, key)
        if mode not in allowed_modes(convo, key):
            raise SlotError("mode is not available on this slot")
        if mode == draft.mode:
            return draft
        if draft.dirty and not confirm:
            raise DirtyDraft("draft has been written")
        draft.snapshot = (draft.mode, draft.text, draft.dirty)
        draft.mode = mode
        draft.text = initial_text(convo, key, mode)
        draft.dirty = False
        return draft

    def undo_switch(self, convo: Conversation, key: SlotKey) -> Draft:
        draft = self.ensure(convo, key)
        if draft.snapshot is None:
            return draft
        mode, text, dirty = draft.snapshot
        draft.mode = mode
        draft.text = text
        draft.dirty = dirty
        draft.snapshot = None
        return draft

    def consume(self, convo: Conversation, key: SlotKey) -> Draft:
        draft = self.items.get(key)
        if draft is None and slot_valid(convo, key):
            draft = self.ensure(convo, key)
        if draft is None or not slot_valid(convo, key):
            # The command removed its own slot (the root slot after the first
            # send is the normal case).  Forget it; the active slot moves on.
            self.items.pop(key, None)
            return Draft(mode=draft.mode if draft is not None else ROOT, text="", dirty=False)
        draft.text = initial_text(convo, key, draft.mode)
        draft.dirty = False
        draft.snapshot = None
        return draft

    def prune(self, convo: Conversation) -> None:
        dead = [key for key in self.items if not slot_valid(convo, key)]
        for key in dead:
            self.items.pop(key, None)
        if self.active is not None and not slot_valid(convo, self.active):
            self.active = None
        if self.active is None:
            self.active = default_key(convo)
        self.ensure(convo, self.active)


@dataclass
class SlotRun:
    key: SlotKey
    mode: str
    write_layer: str
    command: commands.Command
    assistant_id: str | None
    question_id: str | None
    base_id: str | None
    model: str | None
    needs_model: bool
    feedback: str
    messages: list[dict[str, str]] = field(default_factory=list)
    read: list[str] = field(default_factory=list)


def _messages_after(
    convo: Conversation,
    mode: str,
    question_id: str | None,
    base_id: str | None,
    feedback: str,
) -> list[dict[str, str]]:
    if mode in (ROOT, NEW_QUESTION, REVISE_QUESTION):
        assert question_id is not None
        return chain_messages(convo, question_id)
    if mode == NEW_ANSWER:
        q = convo.nodes[question_id] if question_id else None
        leaf = q.id if q is not None else (convo.view_leaf() or "")
        msgs = chain_messages(convo, leaf) if leaf else []
        if feedback:
            msgs.append({"role": "user", "content": feedback})
        return msgs
    if mode == REWRITE_ANSWER:
        q = convo.nodes[question_id] if question_id else None
        leaf = q.id if q is not None else (convo.view_leaf() or "")
        msgs = chain_messages(convo, leaf) if leaf else []
        if feedback:
            base = convo.nodes.get(base_id) if base_id else None
            if base is not None and base.text.strip():
                msgs.append({"role": "assistant", "content": base.text})
            msgs.append({"role": "user", "content": feedback})
        return msgs
    return []


def execute_slot(convo: Conversation, key: SlotKey, draft: Draft) -> SlotRun:
    if not slot_valid(convo, key):
        raise CannotSend("这个槽已经失效")
    if draft.mode not in allowed_modes(convo, key):
        raise CannotSend("模式不可用")
    if not can_send(convo, key, draft):
        raise CannotSend(send_disabled_reason(convo, key, draft) or "不能发送")
    text = draft.text
    feedback = text.strip() if draft.mode in (NEW_ANSWER, REWRITE_ANSWER) else text
    model = convo.model
    assistant_id: str | None = None
    question_id: str | None = None
    base_id = key.base_id
    needs_model = True

    if draft.mode == ROOT:
        question_id = new_id()
        assistant_id = new_id()
        command = commands.Composite(
            [
                commands.CreateRoot(text, node_id=question_id, model=model),
                commands.AddChild(
                    question_id,
                    "assistant",
                    status="streaming",
                    node_id=assistant_id,
                    model=model,
                ),
            ],
            inverse=None,
            irreversible=True,
            label="root question",
        )
        write_layer = ROOT_SCOPE
    elif draft.mode in (NEW_QUESTION, REVISE_QUESTION):
        parent = key.parent_id
        question_id = new_id()
        assistant_id = new_id()
        command = commands.Composite(
            [
                commands.AddChild(parent, "user", text, node_id=question_id, model=model),
                commands.AddChild(
                    question_id,
                    "assistant",
                    status="streaming",
                    node_id=assistant_id,
                    model=model,
                ),
            ],
            inverse=commands.CloseChild(parent, question_id),
            label="new question",
        )
        write_layer = parent
    elif draft.mode in (NEW_ANSWER, REWRITE_ANSWER):
        q = key.parent_id
        assistant_id = new_id()
        command = commands.AddChild(
            q,
            "assistant",
            status="streaming",
            node_id=assistant_id,
            model=model,
        )
        write_layer = q
        question_id = q
    elif draft.mode == EDIT_ANSWER:
        base = convo.nodes.get(key.base_id) if key.base_id else None
        if base is None or base.parent_id is None:
            raise CannotSend("底稿不在可写的层里")
        q = base.parent_id
        assistant_id = new_id()
        command = commands.AddChild(
            q,
            "assistant",
            text=text,
            status="complete",
            node_id=assistant_id,
            model=model,
        )
        write_layer = q
        question_id = q
        needs_model = False
    else:
        raise CannotSend("模式不可用")

    commands.execute(convo, command)
    messages = _messages_after(convo, draft.mode, question_id, base_id, feedback)
    if draft.mode in (ROOT, NEW_QUESTION, REVISE_QUESTION):
        read_ids = [n.id for n in convo.chain(question_id)]
    elif draft.mode in (NEW_ANSWER, REWRITE_ANSWER):
        read_ids = [n.id for n in convo.chain(question_id)] if question_id else []
        if draft.mode == REWRITE_ANSWER and feedback and base_id:
            read_ids.append(base_id)
    else:
        read_ids = []
    return SlotRun(
        key=key,
        mode=draft.mode,
        write_layer=write_layer,
        command=command,
        assistant_id=assistant_id,
        question_id=question_id,
        base_id=base_id,
        model=model,
        needs_model=needs_model,
        feedback=feedback,
        messages=messages,
        read=read_ids,
    )


def propose_question_messages(convo: Conversation, key: SlotKey, text: str) -> list[dict[str, str]]:
    """Draft action: ask the model for a rephrasing; result never becomes a node."""
    if key.parent_id is None:
        return []
    msgs = chain_messages(convo, key.parent_id)
    msgs.append(
        {
            "role": "user",
            "content": (
                "请把下面这个问题改写得更清楚、更具体，保留原意。"
                "只输出改写后的问句，不要解释：\n\n" + text.strip()
            ),
        }
    )
    return msgs
