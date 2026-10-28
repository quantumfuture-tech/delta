import os, sys, time, shutil
sys.path.insert(0, "D:/LMY/coding/aichat/a")
os.environ["QT_QPA_PLATFORM"] = "windows"
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent
from PySide6.QtGui import QImage
from delta.core.model import Conversation
from delta.core.store import Store
from delta.ui import theme
from delta.ui.main_window import MainWindow

class FakeClient:
    api_key = "sk-shot"
    def __init__(self, chunks, delay=0.02):
        self.chunks = chunks; self.delay = delay
    def stream(self, messages, model, cancel=None, on_delta=None):
        from delta.core.client import StreamResult
        text = ""
        for c in self.chunks:
            if cancel is not None and cancel.is_set():
                return StreamResult(text=text, status="cancelled")
            on_delta("content", c); text += c; time.sleep(self.delay)
        return StreamResult(text=text, status="complete", finish_reason="stop")

app = QApplication(sys.argv[:1]); theme.apply(app)
DATA = Path("D:/LMY/coding/aichat/a/build/fork_shot_data")
shutil.rmtree(DATA, ignore_errors=True)
store = Store(DATA)
c = Conversation(title="重试之后，两条答案都还在")

u1 = c.add_user("我在做一个会分叉的对话客户端。用三点说明：为什么界面只渲染一条链，比分叉时重画整棵树更好？")
a1 = c.add_assistant(u1.id, status="complete", model="deepseek-reasoner")
a1.text = """**读者只有一个位置**：人正在读的是一条路，不是一张地图。
**树可以留在旁边**：分支、首选与整棵形状交给 ATLAS。
**重排要局部化**：记住链，切换时只换后缀，前面的卡片一个都不动。"""
a1.reasoning = "先区分存储和视图：树是存储，链是视图。如果每次分叉都重画整棵树，滚动位置和阅读上下文都会丢；只维护当前链，把树的结构放到旁边，代价是局部导航需要额外的一层。"

c.set_pointer(a1.id)
b1 = c.add_user("换个角度：如果分叉收费，产品会怎么设计？")
b2 = c.add_assistant(b1.id, status="complete")
b2.text = "那它会先卖「后悔药」：把编辑、重试、对比做成一层层的付费墙。免费的分叉会逼产品回答一个更诚实的问题：人究竟想同时保住哪些念头？"

c.set_pointer(a1.id)
u2 = c.add_user("如果重试不覆盖旧回答，界面会长成什么样？")
a2a = c.add_assistant(u2.id, status="complete")
a2a.text = "它会变成同一节点下的平行分支：旧的答案仍然在，新的答案作为兄弟出现，两个都能被读到。"

c.set_pointer(a2a.id)
store.save(c)
w = MainWindow(store, store.read_settings(), c)
w.resize(1440, 960); w.show()
def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents(); time.sleep(0.008)
pump(400)

w.client = FakeClient(["**重试成为兄弟分支**：", "新的回答不覆盖旧的，", "只作为同一节点下的另一条路径。\n\n", "- 旧的回答完整保留\n", "- 分支条自动出现两个答案\n", "- 首选仍可单独标记\n\n", "> 失败不是覆盖，而是多一个选项。"])
w._retry_node(a2a.id)
t0 = time.time()
while w._job is not None and time.time() - t0 < 10:
    app.processEvents(); time.sleep(0.01)
pump(300)
# settle layout so the screenshot is not mid-reflow
w.transcript._feed.activate()
w.transcript._column.layout().activate()
w.transcript.verticalScrollBar().setValue(0)
w.transcript._at_bottom = False
w.composer.focus_input()
for card in list(w.transcript._cards.values()):
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    card.panel.set_hover(False)
app.processEvents()
w.repaint()
bar = w.transcript._forks.get(u2.id)
print("fork texts", bar.chip_texts() if bar else None)
print("chain", [n.role for n in c.chain()], "scroll", w.transcript.verticalScrollBar().value(), w.transcript.verticalScrollBar().maximum())
w.grab().save("D:/LMY/coding/aichat/a/build/fork_shot.png")
# crop the lower fork + answer area for the bug-fix evidence
img = QImage("D:/LMY/coding/aichat/a/build/fork_shot.png")
img.copy(150, 390, 930, 420).save("D:/LMY/coding/aichat/a/docs/screenshots/fork_after_retry.png")
w.close()