import os, sys, time, shutil
sys.path.insert(0, "D:/LMY/coding/aichat/a")
os.environ["QT_QPA_PLATFORM"] = "windows"
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import QEvent
from delta.core.model import Conversation
from delta.core.store import Store
from delta.ui import theme
from delta.ui.main_window import MainWindow
app = QApplication(sys.argv[:1]); theme.apply(app)
DATA=Path("D:/LMY/coding/aichat/a/build/fork_rows_shot"); shutil.rmtree(DATA, ignore_errors=True)
store=Store(DATA)
c=Conversation(title="五个分支，两行都还在")
u1=c.add_user("同一个问题，可以长出多少条路？")
a1=c.add_assistant(u1.id, status="complete")
a1.text="先别急着选。把五种可能的取径都摆在同一张桌子上，再决定哪一条值得继续；分支应该便宜到允许你这样浪费一次注意力。"
c.set_pointer(a1.id)
children=[]
for i in range(5):
    c.set_pointer(a1.id)
    child=c.add_user(("取径 %d：" % (i+1)) + "这是一条足够长的候选路径，让气泡换行后仍然完整可见。" * 2)
    children.append(child)
c.set_pointer(children[2].id)
store.save(c)
w=MainWindow(store, store.read_settings(), c); w.resize(1440,960); w.show()
def pump(ms):
    end=time.time()+ms/1000
    while time.time()<end: app.processEvents(); time.sleep(0.008)
pump(400)
for card in list(w.transcript._cards.values()):
    card.leaveEvent(QEvent(QEvent.Type.Leave)); card.panel.set_hover(False)
w.composer.focus_input(); app.processEvents(); w.repaint()
bar=w.transcript._forks.get(a1.id)
print("bar", bar.width() if bar else None, bar.height() if bar else None, "flow", bar._flow_host.height() if bar else None, "chips", len(bar._chips) if bar else None)
print("inside", [((ch.mapTo(bar, ch.rect().topLeft()).y(), ch.height()), ch.isVisible()) for ch in bar._chips] if bar else None)
w.grab().save("D:/LMY/coding/aichat/a/build/fork_rows_shot.png")
img=QImage("D:/LMY/coding/aichat/a/build/fork_rows_shot.png")
# crop around the two-row fork bar and its context
img.copy(150, 260, 1050, 380).save("D:/LMY/coding/aichat/a/docs/screenshots/fork_two_rows.png")
w.close()