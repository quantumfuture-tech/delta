"""A daemon thread that owns one SSE stream.

Deliberately not a QThread: the UI only ever sees a thread-safe queue, and a
daemon thread cannot keep the process alive if the window is closed mid-stream.
"""
from __future__ import annotations

import queue
import threading

from ..core.client import ChatClient, StreamError, StreamResult


class StreamJob:
    def __init__(self, client: ChatClient, messages: list[dict[str, str]], model: str) -> None:
        self.client = client
        self.messages = messages
        self.model = model
        self.cancel = threading.Event()
        self.queue: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="delta-stream", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            result = self.client.stream(
                self.messages,
                self.model,
                cancel=self.cancel,
                on_delta=lambda kind, text: self.queue.put((kind, text)),
            )
        except Exception as exc:  # never let a worker die silently
            result = StreamResult(status="error", error=StreamError("server", str(exc)))
        self.queue.put(("__done__", result))


class NullJob:
    """Used by screenshots/tests to feed a prepared stream without a network."""

    def __init__(self, chunks: list[tuple[str, str]], result: StreamResult) -> None:
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        for kind, text in chunks:
            self.queue.put((kind, text))
        self.queue.put(("__done__", result))

    def start(self) -> None:
        return
