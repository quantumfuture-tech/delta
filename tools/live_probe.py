import sys, time
sys.path.insert(0, "D:/LMY/coding/aichat/a")
from delta.core.client import ChatClient
c = ChatClient()
print("key", bool(c.api_key), len(c.api_key), "endpoint", c.endpoint)
t0 = time.time()
seen = []
r = c.stream([{"role":"user","content":"只回答四个字：分叉很好。"}], "deepseek-chat", on_delta=lambda k,t: seen.append((k,t)))
print("status", r.status, "err", (r.error.kind if r.error else None), "len", len(r.text), "tokens", len(seen), "elapsed", round(time.time()-t0,2))
print("text", repr(r.text[:80]))
print("finish", r.finish_reason, "usage", r.usage)
