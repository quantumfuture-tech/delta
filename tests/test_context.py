from delta.core.context import chain_messages
from delta.core.model import Conversation


def test_branch_isolation_two_forks_deep():
    c = Conversation()
    u = c.add_user("root")
    a = c.add_assistant(u.id, status="complete")
    a.text = "A"
    left = c.add_user("left")
    la = c.add_assistant(left.id, status="complete")
    la.text = "LA"
    c.set_pointer(a.id)
    right = c.add_user("right")
    ra = c.add_assistant(right.id, status="complete")
    ra.text = "RA"
    assert [m["content"] for m in chain_messages(c)] == ["root", "A", "right", "RA"]
    assert [m["content"] for m in chain_messages(c, la.id)] == ["root", "A", "left", "LA"]


def test_reasoning_is_not_sent_back():
    c = Conversation()
    u = c.add_user("q")
    a = c.add_assistant(u.id, status="complete")
    a.text = "answer"
    a.reasoning = "private chain of thought"
    msgs = chain_messages(c)
    assert "private chain of thought" not in str(msgs)
    assert msgs[-1] == {"role": "assistant", "content": "answer"}
