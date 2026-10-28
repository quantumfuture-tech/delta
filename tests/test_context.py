from delta.core.context import chain_messages
from delta.core.model import Conversation


def test_branch_isolation_two_forks_deep():
    c = Conversation()
    u = c.create(None, "user", "root")
    a = c.create(c.layer(u.id), "assistant", "A")
    left = c.create(c.layer(a.id), "user", "left")
    la = c.create(c.layer(left.id), "assistant", "LA")
    right = c.create(c.layer(a.id), "user", "right")
    ra = c.create(c.layer(right.id), "assistant", "RA")
    c.activate(a.id, right.id)
    assert [m["content"] for m in chain_messages(c)] == ["root", "A", "right", "RA"]
    assert [m["content"] for m in chain_messages(c, la.id)] == ["root", "A", "left", "LA"]
    assert [n.id for n in c.view()] == [u.id, a.id, right.id, ra.id]


def test_reasoning_is_not_sent_back():
    c = Conversation()
    u = c.create(None, "user", "q")
    a = c.create(c.layer(u.id), "assistant", "answer")
    a.reasoning = "private chain of thought"
    msgs = chain_messages(c)
    assert "private chain of thought" not in str(msgs)
    assert msgs[-1] == {"role": "assistant", "content": "answer"}
