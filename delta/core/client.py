"""Streaming client for any OpenAI-compatible chat endpoint.

Deliberately built on the standard library: an SSE stream is a few lines of
text, and owning the read loop gives us exact control over cancellation,
partial-content preservation and the error taxonomy the UI needs.

No Qt, no third-party dependencies.
"""
from __future__ import annotations

import codecs
import http.client
import json
import os
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Callable, Literal
from urllib import error as urlerror
from urllib import request as urlrequest

from .model import DEFAULT_MODEL

ErrorKind = Literal[
    "auth", "rate_limit", "timeout", "connection", "server", "bad_request", "protocol", "config"
]
DeltaCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class StreamError:
    kind: ErrorKind
    message: str
    status: int | None = None
    retry_after: float | None = None
    detail: str = ""

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "timeout", "connection", "server", "protocol")


@dataclass
class StreamResult:
    text: str = ""
    reasoning: str = ""
    status: str = "complete"  # complete | cancelled | error
    error: StreamError | None = None
    finish_reason: str | None = None
    usage: dict | None = None


def _body_detail(body: bytes) -> tuple[str, str]:
    """Return (human message, raw detail) from an error response body."""
    text = body.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(text)
        err = obj.get("error") if isinstance(obj, dict) else None
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("type") or "")
            return msg[:400], text[:2000]
        if isinstance(err, str):
            return err[:400], text[:2000]
        if isinstance(obj, dict) and obj.get("message"):
            return str(obj["message"])[:400], text[:2000]
    except ValueError:
        pass
    return text[:400], text[:2000]


def _retry_after(headers) -> float | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def classify_http(status: int, headers, body: bytes) -> StreamError:
    message, detail = _body_detail(body)
    if status in (401, 403):
        return StreamError("auth", message or "unauthorized", status, None, detail)
    if status == 429:
        return StreamError("rate_limit", message or "rate limited", status, _retry_after(headers), detail)
    if 400 <= status < 500:
        return StreamError("bad_request", message or f"HTTP {status}", status, None, detail)
    if status >= 500:
        return StreamError("server", message or f"HTTP {status}", status, None, detail)
    return StreamError("server", message or f"HTTP {status}", status, None, detail)


class ChatClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def stream(
        self,
        messages: list[dict[str, str]],
        model: str = DEFAULT_MODEL,
        *,
        cancel: threading.Event | None = None,
        on_delta: DeltaCallback | None = None,
        timeout: float | None = None,
    ) -> StreamResult:
        result = StreamResult()
        if not self.api_key:
            result.status = "error"
            result.error = StreamError(
                "config",
                "DEEPSEEK_API_KEY is not set",
            )
            return result
        if not messages:
            result.status = "error"
            result.error = StreamError("config", "empty context")
            return result
        if cancel is not None and cancel.is_set():
            result.status = "cancelled"
            return result

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urlrequest.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "Delta/0.1",
            },
        )
        read_timeout = float(timeout if timeout is not None else self.timeout)
        response = None
        done = False
        finish_reason = None
        buf = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        def emit(kind: str, text: str) -> None:
            if not text:
                return
            if kind == "content":
                result.text += text
            elif kind == "reasoning":
                result.reasoning += text
            else:
                return
            if on_delta is not None:
                try:
                    on_delta(kind, text)
                except Exception:
                    pass

        def finish_error(kind: ErrorKind, message: str, status=None, retry=None, detail="") -> StreamResult:
            result.status = "error"
            result.error = StreamError(kind, message, status, retry, detail)
            result.finish_reason = finish_reason
            return result

        try:
            response = urlrequest.urlopen(req, timeout=read_timeout, context=ssl.create_default_context())
        except urlerror.HTTPError as exc:
            try:
                body = exc.read(32768)
            except Exception:
                body = b""
            result.status = "error"
            result.error = classify_http(exc.code, exc.headers, body)
            return result
        except (socket.timeout, TimeoutError):
            return finish_error("timeout", "request timed out")
        except urlerror.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return finish_error("timeout", "request timed out")
            if isinstance(reason, ssl.SSLError):
                return finish_error("connection", "TLS handshake failed", detail=str(reason))
            return finish_error("connection", "cannot reach the endpoint", detail=str(reason))
        except (ConnectionError, http.client.HTTPException, OSError) as exc:
            return finish_error("connection", "cannot reach the endpoint", detail=str(exc))
        except ValueError as exc:
            return finish_error("config", "invalid endpoint", detail=str(exc))

        reader = getattr(response, "read1", None) or response.read
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    result.status = "cancelled"
                    result.finish_reason = finish_reason
                    return result
                try:
                    chunk = reader(65536)
                except (socket.timeout, TimeoutError):
                    result.status = "error"
                    result.error = StreamError("timeout", "stream went silent")
                    result.finish_reason = finish_reason
                    return result
                except (ConnectionError, http.client.HTTPException, OSError, ssl.SSLError) as exc:
                    result.status = "error"
                    result.error = StreamError("connection", "connection interrupted", detail=str(exc))
                    result.finish_reason = finish_reason
                    return result
                if not chunk:
                    break
                buf += decoder.decode(chunk)
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line or line.startswith(":") or line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_line = line[5:].strip()
                    if not data_line:
                        continue
                    if data_line == "[DONE]":
                        done = True
                        break
                    try:
                        event = json.loads(data_line)
                    except ValueError:
                        result.status = "error"
                        result.error = StreamError("protocol", "malformed stream event", detail=data_line[:500])
                        result.finish_reason = finish_reason
                        return result
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        err = event["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        result.status = "error"
                        result.error = StreamError("server", str(msg or "stream error")[:400], detail=data_line[:2000])
                        result.finish_reason = finish_reason
                        return result
                    if event.get("usage"):
                        result.usage = event["usage"]
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        delta = {}
                    reasoning = delta.get("reasoning_content")
                    content = delta.get("content")
                    if isinstance(reasoning, str):
                        emit("reasoning", reasoning)
                    if isinstance(content, str):
                        emit("content", content)
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = str(fr)
                if done:
                    break
        finally:
            try:
                response.close()
            except Exception:
                pass

        # Flush any partially decoded bytes and the final line.
        tail = decoder.decode(b"", final=True)
        if tail:
            buf += tail
        if buf.strip() and not done:
            # A final JSON line without a trailing newline is legitimate.
            line = buf.strip()
            if line.startswith("data:"):
                data_line = line[5:].strip()
                if data_line == "[DONE]":
                    done = True
                elif data_line:
                    try:
                        event = json.loads(data_line)
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        if event.get("error"):
                            err = event["error"]
                            msg = err.get("message") if isinstance(err, dict) else str(err)
                            result.status = "error"
                            result.error = StreamError("server", str(msg or "stream error")[:400])
                            return result
                        choices = event.get("choices") or []
                        if choices:
                            choice = choices[0] or {}
                            delta = choice.get("delta") or {}
                            if isinstance(delta.get("reasoning_content"), str):
                                emit("reasoning", delta["reasoning_content"])
                            if isinstance(delta.get("content"), str):
                                emit("content", delta["content"])
                            if choice.get("finish_reason"):
                                finish_reason = str(choice["finish_reason"])

        if cancel is not None and cancel.is_set():
            result.status = "cancelled"
        elif done or finish_reason:
            result.status = "complete"
        else:
            result.status = "error"
            result.error = StreamError("connection", "stream ended before completion")
        result.finish_reason = finish_reason
        return result
