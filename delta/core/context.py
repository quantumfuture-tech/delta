"""Turn a conversation pointer into an OpenAI-compatible message list.

Only the chain root -> pointer is sent.  Sibling branches are invisible to the
model, which is the whole point of branching for free.
"""
from __future__ import annotations

from typing import Any

from .model import Conversation, Node


def chain_messages(convo: Conversation, leaf_id: str | None = None) -> list[dict[str, str]]:
    chain = convo.chain(leaf_id)
    out: list[dict[str, str]] = []
    for node in chain:
        if node.role == "assistant":
            if not node.text.strip():
                # A placeholder that never received a token is not a turn.
                continue
            out.append({"role": "assistant", "content": node.text})
        else:
            out.append({"role": "user", "content": node.text})
    return out


def context_size(convo: Conversation, leaf_id: str | None = None) -> int:
    return len(chain_messages(convo, leaf_id))


def context_turns(convo: Conversation, leaf_id: str | None = None) -> int:
    return len(convo.chain(leaf_id))
