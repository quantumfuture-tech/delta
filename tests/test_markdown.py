from delta.ui.markdown_render import strip_for_stream


def test_strip_for_stream_removes_markers():
    raw = "## 标题\n\n**粗体** 和 *斜体* 与 `代码`，[链接](https://example.com)。\n\n> 引用\n\n- 项目"
    out = strip_for_stream(raw)
    assert "**" not in out and "`" not in out
    assert "标题" in out and "粗体" in out and "链接" in out
    assert "https://example.com" not in out
    assert "• 项目" in out


def test_strip_for_stream_removes_fence_lines_but_keeps_code():
    raw = "```python\nprint('hi')\n```"
    out = strip_for_stream(raw)
    assert "```" not in out
    assert "print('hi')" in out
