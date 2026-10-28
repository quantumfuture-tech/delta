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
    a = c.add_assistant(parent_user_id, model=model, status="complete", text=res.text, reasoning=res.reasoning, move=True)
    print(f"{model} {len(res.text)} chars in {round(time.time()-t0,1)}s :: {res.text[:90]!r}")
    return a

def say(prompt, model="deepseek-chat"):
    u = c.add_user(prompt, model=model, move=True)
    a = add_answer(u.id, model)
    return u, a

u1, a1 = say("我在做一个会分叉的对话客户端。请用三个要点解释：为什么把「首选孩子」记成一个指针，比把整条路径另存一份更好？每个要点 45 字左右，用粗体短标签开头，不要开场白。", "deepseek-reasoner")
# branch B: a different follow-up from a1
c.set_pointer(a1.id)
say("反过来想：如果分叉完全免费，会带来什么新的负担？给三点，每点 35 字左右，不要开场白。", "deepseek-chat")
# branch A: chosen follow-up
c.set_pointer(a1.id)
u2, a2 = say("从信息架构的角度，给出三条具体建议，每条 40 字左右，用有序列表。", "deepseek-chat")
# alternative assistant answer under u2: a second answer, no mixed-role fork
c.set_pointer(u2.id)
d2 = add_answer(u2.id, "deepseek-chat")
# chosen path: u1 -> a1 -> u2 -> a2
c.set_pointer(a2.id)
c.set_preferred(a1.id, u2.id)
c.set_preferred(u2.id, a2.id)
c.rename("分叉，作为一种思考方式")
c.ui["draft"] = "如果把 ATLAS 收起来，界面还成立吗？"
store.save(c)
print("nodes", len(c.nodes), "forks", c.stats()["forks"], "chain", [n.id for n in c.chain()])
print("saved", store.path_for(c.id))
