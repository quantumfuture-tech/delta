from delta.core.context import chain_messages
from delta.core.model import Conversation
from tests.helpers import activate, add, root


def test_branch_isolation_two_forks_deep():
    c = Conversation()
    u = root(c, "root")
    a = add(c, u.id, "assistant", "A")
    left = add(c, a.id, "user", "left")
    la = add(c, left.id, "assistant", "LA")
    right = add(c, a.id, "user", "right")
    ra = add(c, right.id, "assistant", "RA")
    activate(c, a.id, right.id)
    assert [m["content"] for m in chain_messages(c)] == ["root", "A", "right", "RA"]
    assert [m["content"] for m in chain_messages(c, la.id)] == ["root", "A", "left", "LA"]
    assert [n.id for n in c.view()] == [u.id, a.id, right.id, ra.id]


def test_reasoning_is_not_sent_back():
    c = Conversation()
    u = root(c, "q")
    a = add(c, u.id, "assistant", "answer")
    a.reasoning = "private chain of thought"
    msgs = chain_messages(c)
    assert "private chain of thought" not in str(msgs)
    assert msgs[-1] == {"role": "assistant", "content": "answer"}
