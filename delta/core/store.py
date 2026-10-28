"""Small, atomic, human-readable persistence.

Each conversation is one JSON file.  Writes go to a temp file in the same
directory, are fsynced, then os.replace()d, so a crash never leaves a
half-written document.  Deletion moves the file to trash/ rather than
unlinking it.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .model import Conversation


class StoreError(RuntimeError):
    pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_id(conversation_id: str) -> str:
    cleaned = "".join(ch for ch in conversation_id if ch.isalnum() or ch in "_-")
    if not cleaned or cleaned != conversation_id:
        raise StoreError("invalid conversation id")
    return cleaned


class Store:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.conversations_dir = self.root / "conversations"
        self.trash_dir = self.root / "trash"
        self.settings_path = self.root / "settings.json"
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    # -- conversations -------------------------------------------------
    def path_for(self, conversation_id: str) -> Path:
        return self.conversations_dir / (_safe_id(conversation_id) + ".json")

    def save(self, convo: Conversation) -> Path:
        path = self.path_for(convo.id)
        payload = json.dumps(convo.to_dict(), ensure_ascii=False, indent=None, separators=(",", ":"))
        try:
            _atomic_write(path, payload)
        except OSError as exc:
            raise StoreError(f"cannot save conversation: {exc}") from exc
        return path

    def load(self, conversation_id: str) -> Conversation:
        path = self.path_for(conversation_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StoreError(f"conversation not found: {conversation_id}") from None
        except (OSError, ValueError) as exc:
            raise StoreError(f"cannot read conversation: {exc}") from exc
        try:
            return Conversation.from_dict(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(f"corrupt conversation: {exc}") from exc

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.conversations_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                convo = Conversation.from_dict(raw)
                item = convo.summary()
                item["path"] = str(path)
                items.append(item)
            except Exception as exc:  # one broken file must not hide the library
                items.append(
                    {
                        "id": path.stem,
                        "title": "损坏的对话",
                        "updated_at": path.stat().st_mtime if path.exists() else 0,
                        "nodes": 0,
                        "forks": 0,
                        "depth": 0,
                        "preview": str(exc),
                        "corrupt": True,
                        "path": str(path),
                    }
                )
        items.sort(key=lambda it: it.get("updated_at") or 0, reverse=True)
        return items

    def delete(self, conversation_id: str) -> None:
        path = self.path_for(conversation_id)
        if not path.exists():
            raise StoreError("conversation not found")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.trash_dir / (path.stem + "-" + stamp + ".json")
        try:
            shutil.move(str(path), str(dest))
        except OSError as exc:
            raise StoreError(f"cannot move conversation to trash: {exc}") from exc

    # -- settings ------------------------------------------------------
    def read_settings(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, OSError, ValueError):
            return {}

    def write_settings(self, settings: dict[str, Any]) -> None:
        try:
            _atomic_write(
                self.settings_path,
                json.dumps(settings, ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            raise StoreError(f"cannot save settings: {exc}") from exc

    def update_settings(self, **patch: Any) -> dict[str, Any]:
        settings = self.read_settings()
        settings.update(patch)
        self.write_settings(settings)
        return settings
