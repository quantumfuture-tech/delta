import os, sys, time, shutil
sys.path.insert(0, "D:/LMY/coding/aichat/a")
from pathlib import Path
from delta.core.client import ChatClient
from delta.core.context import chain_messages
from delta.core.model import Conversation
from delta.core.store import Store

DATA = Path("D:/LMY/coding/aichat/a/build/demo_data")
shutil.rmtree(DATA, ignore_errors=True)
store = Store(DATA)
client = ChatClient()
print("key", bool(client.api_key))
c = Conversation(title="分叉，作为一种思考方式")


def add_answer(parent_user_id, model="deepseek-chat"):
    msgs = chain_messages(c, parent_user_id)
    t0 = time.time()
    res = client.stream(msgs, model)
    if res.status != "complete":
        print("ERROR", res.status, res.error)
        return None
    a = c.create(
        c.layer(parent_user_id),
        "assistant",
        res.text,
        model=model,
        reasoning=res.reasoning,
        status="complete",
    )
    print(f"{model} {len(res.text)} chars in {round(time.time()-t0,1)}s :: {res.text[:90]!r}")
    return a


def ask(prompt, parent_id, model="deepseek-chat"):
    u = c.create(c.layer(parent_id), "user", prompt, model=model)
    a = add_answer(u.id, model)
    return u, a


u1 = c.create(None, "user", "我在做一个会分叉的对话客户端。请用三个要点解释：为什么把「默认孩子」记成一个指针，比把整条路径另存一份更好？每个要点 45 字左右，用粗体短标签开头，不要开场白。", model="deepseek-reasoner")
a1 = add_answer(u1.id, "deepseek-reasoner")
# branch B: a different follow-up from a1
uB, aB = ask("反过来想：如果分叉完全免费，会带来什么新的负担？给三点，每点 35 字左右，不要开场白。", a1.id, "deepseek-chat")
# branch A: chosen follow-up
u2, a2 = ask("从信息架构的角度，给出三条具体建议，每条 40 字左右，用有序列表。", a1.id, "deepseek-chat")
# alternative assistant answer under u2: a second answer in the same layer
d2 = add_answer(u2.id, "deepseek-chat")
# chosen path: u1 -> a1 -> u2 -> a2
c.activate(a1.id, u2.id)
if a2 is not None:
    c.activate(u2.id, a2.id)
c.rename("分叉，作为一种思考方式")
c.ui["draft"] = "如果把 ATLAS 收起来，界面还成立吗？"
store.save(c)
print("nodes", len(c.nodes), "forks", c.stats()["forks"], "view", [n.id for n in c.view()])
print("saved", store.path_for(c.id))
