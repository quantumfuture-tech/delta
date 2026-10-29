"""Optional UI tracing, for reports that need evidence instead of a guess.

Off unless `DELTA_UI_TRACE` is set.  Writes one line per event to
`%APPDATA%/Delta/ui-trace.log` (or to the path in the variable), so a user can
reproduce an elusive flash and hand over a log that says what actually appeared.
"""
from __future__ import annotations

import os
from pathlib import Path
from time import strftime


def enabled() -> bool:
    return bool(os.environ.get("DELTA_UI_TRACE"))


def path() -> Path:
    setting = os.environ.get("DELTA_UI_TRACE", "")
    if setting in ("", "1"):
        base = os.environ.get("APPDATA")
        root = Path(base) / "Delta" if base else Path.home() / ".delta"
        return root / "ui-trace.log"
    return Path(setting)


def write(text: str) -> None:
    if not enabled():
        return
    stamp = strftime("%H:%M:%S") + f".{int(strftime('%f')[:3]):03d}"
    try:
        with path().open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


def mark(text: str) -> None:
    write(f"---- {text}")
