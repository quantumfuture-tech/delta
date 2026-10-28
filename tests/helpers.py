"""Test helpers: everything writes through commands, never through raw doors."""
from __future__ import annotations

from delta.core import commands, slots
from delta.core.model import Conversation, ROOT_SCOPE


def add(convo: Conversation, parent, role, text: str = "", **kwargs):
    return commands.add_child(convo, parent, role, text, **kwargs)


def root(convo: Conversation, text: str, **kwargs):
    return commands.add_root(convo, text, **kwargs)


def activate(convo: Conversation, parent, child) -> None:
    commands.set_active(convo, parent, child)


def close(convo: Conversation, parent, child) -> int:
    return commands.close(convo, parent, child)


def reorder(convo: Conversation, parent, child, index: int) -> None:
    commands.reorder(convo, parent, child, index)


def raw_remove(convo: Conversation, node_id: str) -> int:
    parent = convo.nodes[node_id].parent_id
    with convo._writing(parent if parent is not None else ROOT_SCOPE):
        return convo._remove_subtree(node_id)


def draft_for(convo: Conversation, key, mode=None, text: str | None = None):
    store = slots.DraftStore()
    draft = store.ensure(convo, key, mode)
    if text is not None:
        draft.text = text
        draft.dirty = True
    return store, draft


def send(convo: Conversation, text: str):
    key = slots.default_key(convo)
    store, draft = draft_for(convo, key, text=text)
    run = slots.execute_slot(convo, key, draft)
    return convo.get(run.question_id), convo.get(run.assistant_id)


def ask(convo: Conversation, parent_id: str, text: str):
    key = slots.SlotKey(parent_id, None)
    store, draft = draft_for(convo, key, slots.NEW_QUESTION, text)
    run = slots.execute_slot(convo, key, draft)
    return convo.get(run.question_id), convo.get(run.assistant_id)


def answer(convo: Conversation, parent_id: str):
    key = slots.SlotKey(parent_id, None)
    store, draft = draft_for(convo, key, slots.NEW_ANSWER, "")
    run = slots.execute_slot(convo, key, draft)
    return convo.get(run.assistant_id)
