import os, sys, time, statistics, platform
sys.path.insert(0, "D:/LMY/coding/aichat/a")
os.environ["QT_QPA_PLATFORM"] = "windows"
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from delta.core import commands
from delta.core.model import Conversation
from delta.core.store import Store
from delta.ui import theme
from delta.ui.main_window import MainWindow

app = QApplication(sys.argv[:1]); theme.apply(app)
DATA = Path("D:/LMY/coding/aichat/a/build/bench_data")
import shutil; shutil.rmtree(DATA, ignore_errors=True)
store = Store(DATA)
c = Conversation(title="perf")
u = commands.add_root(c, "性能测试：一条足够长的消息")
a = commands.add_child(c, u.id, "assistant", status="complete")
a.text = ("分叉不是把树画出来，而是让路径保持可读。" * 60) + "\n\n" + ("这是一段额外的正文，用于模拟较长的流式消息。" * 40)
store.save(c)
w = MainWindow(store, store.read_settings(), c); w.resize(1420, 950); w.show()
def pump(ms):
    end=time.time()+ms/1000
    while time.time()<end:
        app.processEvents(); time.sleep(0.004)
pump(400)
# keep the card at the top; it is tall
w.transcript.verticalScrollBar().setValue(0)
pump(100)

def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals)-1, int(len(vals)*p))]

# --- typing: key event -> glyph paint on the composer
edit = w.composer.edit
edit.clear(); edit.setFocus(); pump(50)
QTest.keyClicks(edit, "warmup")
app.processEvents(); edit.repaint(); edit.clear(); app.processEvents()
typing = []
for i in range(160):
    t0 = time.perf_counter()
    QTest.keyClick(edit, Qt.Key.Key_A)
    app.processEvents()
    edit.repaint()
    typing.append((time.perf_counter()-t0)*1000)

# --- token to screen: append ~5 chars, re-render markdown and paint the body
a.status = "streaming"
w.transcript.rebuild(); w._refresh_stack(); pump(120)
card = w.transcript.card_for(a.id)
assert card is not None
chunks = []
base = a.text
# warm the text-layout/font path without measuring it
a.text += "预热文本。"; w.transcript.update_stream(a.id); app.processEvents(); card.body.repaint()
for i in range(70):
    chunk = "分叉让人同时保住多条思路。"
    a.text += chunk
    t0 = time.perf_counter()
    w.transcript.update_stream(a.id)
    app.processEvents()
    card.body.repaint()
    chunks.append((time.perf_counter()-t0)*1000)
a.status = "complete"; w.transcript.refresh_node(a.id); pump(80)

# --- branch switch: build a layer under the first answer and switch it
u2 = commands.add_child(c, a.id, "user", "左支：从信息架构看")
a2 = commands.add_child(c, u2.id, "assistant", "左支的回答。" * 8, status="complete")
b1 = commands.add_child(c, a.id, "user", "右支：从成本看")
b2 = commands.add_child(c, b1.id, "assistant", "右支的回答。" * 8, status="complete")
commands.set_active(c, a.id, u2.id)
w.transcript.rebuild(); w._refresh_meta(); pump(120)
# one warm-up switch so the first measured switch is not paying cold layout
w._activate(a.id, b1.id)
pump(80)
w._activate(a.id, u2.id)
pump(80)
switches = []
for i in range(20):
    target = b1.id if i % 2 == 0 else u2.id
    t0 = time.perf_counter()
    w._activate(a.id, target)
    app.processEvents()
    switches.append((time.perf_counter()-t0)*1000)

print("machine", platform.platform(), "python", platform.python_version())
print("typing ms p50=%.2f p95=%.2f max=%.2f" % (pct(typing,.5), pct(typing,.95), max(typing)))
print("render ms p50=%.2f p95=%.2f max=%.2f" % (pct(chunks,.5), pct(chunks,.95), max(chunks)))
print("switch ms p50=%.2f p95=%.2f max=%.2f" % (pct(switches,.5), pct(switches,.95), max(switches)))
print("typing n", len(typing), "render n", len(chunks), "switch n", len(switches))
Path("D:/LMY/coding/aichat/a/docs/BENCH.md").write_text(
    "# Benchmark\n\n" +
    "- machine: " + platform.platform() + " / Python " + platform.python_version() + "\n" +
    "- typing (key -> composer paint): p50 %.2f ms, p95 %.2f ms, max %.2f ms\n" % (pct(typing,.5), pct(typing,.95), max(typing)) +
    "- streaming token batch (5 chars -> incremental draft append + body paint): p50 %.2f ms, p95 %.2f ms, max %.2f ms\n" % (pct(chunks,.5), pct(chunks,.95), max(chunks)) +
    "- layer switch (prefix diff + rebuild suffix): p50 %.2f ms, p95 %.2f ms, max %.2f ms\n" % (pct(switches,.5), pct(switches,.95), max(switches)) +
    "\nMethod: one warm-up key sequence, one warm-up streaming append, and one warm-up layer switch precede the measurements; 160 injected key events with an immediate repaint of the composer; 70 streaming batches of 5 Chinese characters appended to an in-flight card with an immediate repaint; 20 alternating layer switches. Real deltas are batched by the app on a 14 ms timer, so the end-to-end p95 is about 14 ms plus the render number above. Layer switches alternate between two already-rendered layers, so the card cache is exercised; the first visit to an unseen layer additionally renders its cards.\n"
)
w._save_now(); w.close()
