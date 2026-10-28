"""Markdown -> HTML tuned for Qt's rich text engine."""
from __future__ import annotations

import re

import markdown as _markdown

_EXTENSIONS = ["fenced_code", "tables", "sane_lists", "nl2br"]
_FENCE = "\x60\x60\x60"


def _code_card(match: re.Match) -> str:
    inner = re.sub(r"</?code[^>]*>", "", match.group(1))
    return (
        '<table width="100%" cellspacing="0" cellpadding="12" '
        'style="background-color:#0E1218; margin:10px 0 14px 0;">'
        '<tr><td><pre style="font-family:\'Cascadia Code\'; font-size:9.4pt; '
        'color:#D6D0C4; margin:0;">' + inner + "</pre></td></tr></table>"
    )


def _quote_card(match: re.Match) -> str:
    inner = match.group(1)
    return (
        '<table width="100%" cellspacing="0" cellpadding="12" '
        'style="background-color:#12161D; margin:9px 0 13px 0;">'
        '<tr><td style="border-left:2px solid #C99A50;">' + inner + "</td></tr></table>"
    )


def markdown_to_html(text: str) -> str:
    source = text or ""
    parser = _markdown.Markdown(extensions=_EXTENSIONS, output_format="html")
    body = parser.convert(source)
    body = re.sub(r"<pre>(.*?)</pre>", _code_card, body, flags=re.S)
    body = re.sub(r"<blockquote>(.*?)</blockquote>", _quote_card, body, flags=re.S)
    return "<html><body>" + body + "</body></html>"


DOC_CSS = """
p { margin: 0 0 9px 0; }
h1 { font-family: Georgia; font-size: 18.5pt; font-weight: normal; color: #F4EAD9; margin: 18px 0 8px 0; }
h2 { font-family: Georgia; font-size: 15pt; font-weight: normal; color: #F1E7D6; margin: 17px 0 7px 0; }
h3 { font-family: Segoe UI Variable Text; font-size: 11.5pt; font-weight: 600; color: #EFE6D6; margin: 15px 0 6px 0; }
h4 { font-family: Segoe UI Variable Text; font-size: 10.5pt; font-weight: 600; color: #E6DCCB; margin: 12px 0 5px 0; }
ul, ol { margin: 2px 0 10px 0; }
li { margin: 0 0 4px 0; }
pre { font-family: 'Cascadia Code'; font-size: 9.4pt; background-color: #0E1218; color: #D6D0C4; }
code { font-family: 'Cascadia Code'; font-size: 9.2pt; background-color: #1B222D; color: #E8BE7C; }
blockquote { color: #A29A8D; margin: 0; }
a { color: #8FB9AA; text-decoration: none; }
hr { color: #232A35; }
table { border-collapse: collapse; margin: 6px 0 12px 0; }
th { background-color: #161C25; color: #EDE6D7; font-weight: 600; }
th, td { border: 1px solid #2A323E; padding: 5px 8px; }
strong { color: #F6EDDD; font-weight: 600; }
em { color: #E7C997; }
"""

_FENCE_LINE = re.compile(r"^[ \t]*" + _FENCE + r"[^\n]*\n?", re.MULTILINE)


def strip_for_stream(text: str) -> str:
    """A fast, lossy rendering for the in-flight message.

    The final message is always rendered from the full markdown.  While tokens
    arrive we only need readable text with the syntax markers taken out; that
    lets the live view append a few characters instead of re-laying out the
    whole document on every token.
    """
    s = text or ""
    s = _FENCE_LINE.sub("", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s{0,3}>\s?", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s{0,3}[-*+]\s+", "• ", s, flags=re.MULTILINE)
    s = s.replace("**", "").replace("__", "").replace("~~", "")
    s = s.replace("`", "")
    return s