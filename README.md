# Delta

**A conversation that can fork.**

一个人对着模型工作。没有联系人，没有消息投递，没有已读回执。只有一条当前路径，和路径旁边那些还活着的念头。

![Delta](docs/screenshots/main.png)

## 30 秒启动

需要 Python 3.11+、PySide6、Markdown。密钥放在 User 环境变量 `DEEPSEEK_API_KEY` 里。

```powershell
cd D:\LMY\coding\aichat\a
.\run.ps1
```

`run.ps1` 会自动从 User 环境变量取出密钥、找到本机解释器并启动。用别的解释器：

```powershell
$env:DELTA_PYTHON = "C:\path\to\python.exe"
.\run.ps1
```

或者：

```powershell
pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY","User")
python -m delta
```

默认数据目录是 `%APPDATA%\Delta`；加 `--data D:\somewhere` 可换。

## 怎么用

- **Enter** 发送，**Shift+Enter** 换行，**Esc** 停止（已收到的半个回答保留）。
- 每条消息左侧的圆点可以点：把指针移到这里，从这句继续。旧的路径不会消失。
- 分叉处出现一条 **分支条**：点一支切过去；点分支条右侧的圆点把它设成**首选续写**——只改这一个节点的这一个字段。生成完成、中断或失败后，分支条会自己更新文字和宽度，不需要点击。
- **Alt+↑ / Alt+↓** 在同一分叉的兄弟分支间切换；**Alt+← / Alt+→** 回到父节点 / 沿首选分支向下。
- 右侧 **ATLAS** 是整棵树的形状；点任意节点跳过去。**Ctrl+M** 收起/展开。
- **Ctrl+N** 新对话，**Ctrl+L** 对话库。

## 它特别在哪

不是把树画成一张需要拖拽的画布，也不是在聊天气泡旁边塞一棵缩进树。

**界面只渲染根到当前指针的一条链**；分叉是链上一个安静的、可点击的“分支条”。切换分支时只替换链的后缀，前方的卡片对象原样保留；切换回来时甚至复用同一个卡片。流式生成时每个 token 只改一个卡片；内容按 14ms 批量上屏，但渲染路径是**追加式**的，不做整棵列表重建。

ATLAS 负责全局形状，正文负责阅读。两者都不抢对方的活。

## 数据与性能

- **指针**：当前位置；可见链永远由父指针回溯得到，不另存路径。
- **首选孩子**：每个节点一个字段，只影响“从它继续时走哪一支”。改它不触碰任何消息或别的节点。
- **持久化**：每次操作原子写 JSON；重启恢复指针、内容、滚动锚点（节点 + 偏移），不是脆弱的像素值。
- **流式**：后台 daemon 线程只把 delta 放进队列；主线程 14ms 批量取一次。流式期先追加**纯文本草稿**，完成时一次性做完整 markdown 排版——这是为了把 token 上屏压在 2 帧内，代价是完成时会看到一次排版“落定”。

![Streaming](docs/screenshots/streaming.png)
![Interrupted](docs/screenshots/interrupted.png)

## 测试

```powershell
python -m pytest -q
```

57 个测试：树语义（首选孩子、删除、指针、上下文隔离）、原子存储、SSE 错误分类/中断保留、UI 的增量 diff（前缀控件身份不变）、重启滚动一致性、快速乱点。结果见 [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)。

分支条自动更新与两行布局的证据：[fork_after_retry.png](docs/screenshots/fork_after_retry.png)、[fork_two_rows.png](docs/screenshots/fork_two_rows.png)。

性能见 [docs/BENCH.md](docs/BENCH.md)：键到字符 p95 ≈ 2.4ms；流式渲染 p95 ≈ 9.6ms（加 14ms 批量仍 <2 帧）；分支切换 p95 ≈ 19.7ms。

## 结构

```
delta/core/     零 Qt：model, context, store, SSE client
delta/ui/       PySide6：transcript, message, fork bar, composer, atlas, ...
tests/          pytest：核心 + UI
docs/           DESIGN, 测试、基准、截图
spikes/         被否决的 V1
tools/          demo、基准、真实模型探针、截图
```

## 已知边界

- Markdown 是 Qt 富文本的子集；流式期间是纯文本草稿，完成时才排版。
- 超长回答（> 约 3000 字）完成时的一次性排版会出现 30–60ms 的落定；token 阶段仍是追加式。
- ATLAS 为小树设计；几百个节点会变密，尚无搜索/缩放。分支条预览只截一行。
- 密钥从环境变量读取；没有内置的密钥保险箱。删除单条消息没有撤销（整份对话删除会进 trash）。
- 单人、单机、单 provider（OpenAI 兼容；默认 DeepSeek）。

## 一句话

**最值得说的是：分叉在这里不是视觉效果，而是一份数据结构的直接投影——一个指针、每个节点一个首选孩子、一条按需回溯的链。** 视图永远只画那一条链；切换只换后缀，流式只碰一个节点，重启只恢复一个锚点。所以“分叉免费”不是口号，而是热路径上可验证的事实。