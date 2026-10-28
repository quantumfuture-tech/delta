"""Visual language: ink, paper and a single warm metal.

Centralised so every custom-painted widget uses the same values.  No default
Qt palette survives; the app sets Fusion + a full stylesheet + custom fonts.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


class C:
    bg = "#0A0C10"
    bg_soft = "#0D1015"
    panel = "#10141B"
    panel_hi = "#141922"
    raised = "#181E28"
    line = "#232A35"
    line_soft = "#1A2029"
    line_hi = "#303948"

    ink = "#EDE6D7"
    ink_soft = "#CDC5B6"
    ink_dim = "#9A9387"
    muted = "#79746B"
    faint = "#55524C"

    brass = "#D8A55A"
    brass_hi = "#F0C27A"
    brass_deep = "#8F6A33"
    brass_dim = "#8F6A33"
    brass_glow = "#3A2C18"

    sage = "#86A99B"
    sage_dim = "#4B6159"
    danger = "#D97763"
    danger_dim = "#48241E"

    code_bg = "#0E1218"
    code_line = "#1D242E"
    selection = "#3B3323"

    # roles
    user_bg = "#12171F"
    user_line = "#242C38"


FONT_UI = "Segoe UI Variable Text"
FONT_DISPLAY = "Georgia"
FONT_CJK = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Code"


def font(family: str = FONT_UI, size: float = 10.5, weight: int = 400, spacing: float = 0.0) -> QFont:
    f = QFont(family)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def color(value: str, alpha: int = 255) -> QColor:
    c = QColor(value)
    c.setAlpha(alpha)
    return c


STYLESHEET = f"""
QWidget {{
    color: {C.ink};
    outline: none;
}}
QMainWindow, QWidget#Root {{ background: {C.bg}; }}
QWidget#Rail {{ background: {C.bg_soft}; border-right: 1px solid {C.line_soft}; }}
QWidget#Header {{ background: {C.bg}; border-bottom: 1px solid {C.line_soft}; }}
QWidget#AtlasPanel {{ background: {C.bg_soft}; border-left: 1px solid {C.line_soft}; }}
QWidget#ComposerShell {{ background: transparent; }}
QToolTip {{
    background: {C.raised};
    color: {C.ink_soft};
    border: 1px solid {C.line_hi};
    padding: 5px 8px;
    font-size: 9pt;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: #2A313C;
    min-height: 42px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: #3B4553; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: transparent; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0px; }}
QMenu {{
    background: {C.panel_hi};
    border: 1px solid {C.line_hi};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 30px 8px 12px;
    border-radius: 6px;
    color: {C.ink_soft};
    font-size: 10pt;
}}
QMenu::item:selected {{ background: {C.raised}; color: {C.ink}; }}
QMenu::separator {{ height: 1px; background: {C.line}; margin: 5px 9px; }}
QMenu::icon {{ padding-left: 6px; }}
QLineEdit {{
    background: {C.panel_hi};
    border: 1px solid {C.line_hi};
    border-radius: 7px;
    padding: 6px 9px;
    selection-background-color: {C.brass_deep};
    selection-color: {C.ink};
}}
QPlainTextEdit {{
    background: transparent;
    border: none;
    selection-background-color: {C.brass_deep};
    selection-color: {C.ink};
}}
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea {{ background: transparent; border: none; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, color(C.bg))
    pal.setColor(QPalette.ColorRole.Base, color(C.panel))
    pal.setColor(QPalette.ColorRole.AlternateBase, color(C.panel_hi))
    pal.setColor(QPalette.ColorRole.Text, color(C.ink))
    pal.setColor(QPalette.ColorRole.WindowText, color(C.ink))
    pal.setColor(QPalette.ColorRole.Button, color(C.panel_hi))
    pal.setColor(QPalette.ColorRole.ButtonText, color(C.ink))
    pal.setColor(QPalette.ColorRole.Highlight, color(C.brass_deep))
    pal.setColor(QPalette.ColorRole.HighlightedText, color(C.ink))
    pal.setColor(QPalette.ColorRole.ToolTipBase, color(C.raised))
    pal.setColor(QPalette.ColorRole.ToolTipText, color(C.ink_soft))
    pal.setColor(QPalette.ColorRole.PlaceholderText, color(C.muted))
    app.setPalette(pal)
    app.setFont(font(FONT_UI, 10.5))
    app.setStyleSheet(STYLESHEET)