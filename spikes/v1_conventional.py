"""V1 spike — the conventional three-pane chat we rejected.

Kept as evidence for docs/DESIGN.md.  It is intentionally ordinary: sidebar,
bubble transcript, tree outline in the margin.  Run it, look at it, and then
read the critique.
"""
import os, sys, time
sys.path.insert(0, "D:/LMY/coding/aichat/a")
os.environ["QT_QPA_PLATFORM"] = "windows"
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QScrollArea, QSizePolicy, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from delta.core.model import Conversation

app = QApplication(sys.argv[:1]); app.setStyle("Fusion")
app.setStyleSheet("""
QMainWindow, QWidget { background:#17191d; color:#e6e6e6; font-family:'Segoe UI'; font-size:10pt; }
QListWidget { background:#1d2025; border:none; border-right:1px solid #2a2e35; padding:8px; }
QListWidget::item { padding:9px; border-radius:5px; }
QListWidget::item:selected { background:#2b3039; }
QTreeWidget { background:#1b1e23; border:none; border-left:1px solid #2a2e35; }
QTreeWidget::item { padding:3px; }
QTextBrowser { border:none; background:transparent; }
QScrollArea { border:none; background:transparent; }
""")
c = Conversation(title="分叉，作为一种思考方式")
u1 = c.add_user("我在设计一个会分叉的对话客户端。最核心的取舍应该是什么？")
a1 = c.add_assistant(u1.id, status="complete"); a1.text = "如果分叉是零成本的，界面首先应该保护读者所在的位置，而不是急着把整棵树画出来。路径是主线，分叉只是旁注。"
c.set_pointer(a1.id)
u2 = c.add_user("如果分叉是免费的，界面会长成什么样？")
a2 = c.add_assistant(u2.id, status="complete"); a2.text = "一条主线，加上若干可以瞥见的旁支。主线保持连续的阅读节奏，旁支只在分叉处留下一个可点击的窄条。"
c.set_pointer(a1.id)
b1 = c.add_user("换个角度：如果分叉收费，产品会怎么设计？")
b2 = c.add_assistant(b1.id, status="complete"); b2.text = "那它会先卖后悔药：把编辑、重试、对比做成一层层付费墙。"
c.set_pointer(a2.id)

w = QMainWindow(); w.setWindowTitle("ChatTree — 分叉对话"); w.resize(1420, 900)
root = QWidget(); w.setCentralWidget(root); outer = QHBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

side = QWidget(); side.setFixedWidth(220); sl = QVBoxLayout(side); sl.setContentsMargins(10,12,10,12)
brand = QLabel("ChatTree"); brand.setFont(QFont("Segoe UI", 14, 600)); sl.addWidget(brand)
newbtn = QLabel("+   新建对话"); newbtn.setStyleSheet("background:#2b3039; border-radius:6px; padding:9px; margin:10px 0;")
sl.addWidget(newbtn)
conv = QListWidget(); conv.setFrameShape(QFrame.Shape.NoFrame)
for t, meta in [("分叉式对话的取舍", "12 条 · 今天"), ("长文改写实验", "8 条 · 昨天"), ("关于记忆的笔记", "23 条 · 周一")]:
    conv.addItem(t)
conv.setCurrentRow(0)
conv.setStyleSheet("QListWidget { background:transparent; border:none; padding:0; } QListWidget::item { padding:10px; margin-bottom:2px; }")
sl.addWidget(conv, 1)
hint = QLabel("所有对话都在本地"); hint.setStyleSheet("color:#6f7680; font-size:8pt;")
sl.addWidget(hint)
outer.addWidget(side)

center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)
head = QWidget(); head.setFixedHeight(68); hl = QHBoxLayout(head); hl.setContentsMargins(28,0,28,0)
title = QLabel("分叉式对话的取舍"); title.setFont(QFont("Segoe UI", 15, 600)); hl.addWidget(title)
hl.addStretch(1)
model = QLabel("deepseek-chat  ▾"); model.setStyleSheet("background:#23272e; border-radius:6px; padding:6px 10px; color:#cfd3da;")
hl.addWidget(model)
cl.addWidget(head)

scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
feedw = QWidget(); feed = QVBoxLayout(feedw); feed.setContentsMargins(40, 20, 40, 20); feed.setSpacing(16); feed.setAlignment(Qt.AlignmentFlag.AlignTop)
def bubble(text, role):
    row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0,0,0,0)
    b = QTextBrowser(); b.setFixedWidth(620); b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    b.setText(text); b.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); b.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    b.document().setTextWidth(596)
    b.setFixedHeight(int(b.document().size().height()) + 42)
    if role == "user":
        b.setStyleSheet("QTextBrowser { background:#2f4b8f; color:#f2f5ff; border-radius:12px; padding:12px; }")
        rl.addStretch(1); rl.addWidget(b)
    else:
        b.setStyleSheet("QTextBrowser { background:#22262c; color:#e8e8e8; border-radius:12px; padding:12px; }")
        rl.addWidget(b); rl.addStretch(1)
    feed.addWidget(row)
bubble(u1.text, "user"); bubble(a1.text, "assistant"); bubble(u2.text, "user"); bubble(a2.text, "assistant")
scroll.setWidget(feedw); cl.addWidget(scroll, 1)

comp = QWidget(); comp.setFixedHeight(78); co = QHBoxLayout(comp); co.setContentsMargins(40, 10, 40, 18)
edit = QLabel("写下你的下一条…"); edit.setStyleSheet("background:#22262c; border:1px solid #333942; border-radius:10px; padding:12px; color:#7d838d;")
co.addWidget(edit, 1)
send = QLabel("发送"); send.setFixedSize(70, 42); send.setAlignment(Qt.AlignmentFlag.AlignCenter); send.setStyleSheet("background:#3b5bdb; border-radius:8px; color:white;")
co.addWidget(send)
cl.addWidget(comp)
outer.addWidget(center, 1)

tree = QTreeWidget(); tree.setHeaderHidden(True); tree.setFixedWidth(230); tree.setIndentation(14)
tree.setStyleSheet("QTreeWidget { background:#1b1e23; border-left:1px solid #2a2e35; padding:10px; }")
rootitem = QTreeWidgetItem(["对话树"]); tree.addTopLevelItem(rootitem)
def add(parent, node, label):
    it = QTreeWidgetItem([label]); parent.addChild(it)
    for ch in c.children(node.id):
        role = "你" if ch.role == "user" else "AI"
        add(it, ch, f"{role} · {(ch.text or '生成中')[:18]}")
add(rootitem, c.nodes[u1.id], "你 · 我在设计一个会分叉的…")
rootitem.setExpanded(True); tree.expandAll()
outer.addWidget(tree)

w.show()
end = time.time() + 0.6
while time.time() < end:
    app.processEvents(); time.sleep(0.01)
os.makedirs("D:/LMY/coding/aichat/a/docs/screenshots", exist_ok=True)
w.grab().save("D:/LMY/coding/aichat/a/docs/screenshots/v1_conventional.png")
print("v1 captured", w.size())
w.close()