import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from delta.core.client import ChatClient, StreamError, classify_http


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_payload = None
    last_auth = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, status, obj, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _event(self, obj):
        raw = obj if isinstance(obj, bytes) else ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        try:
            self.wfile.write(raw)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            # The cancellation test deliberately closes the socket mid-stream.
            self.close_connection = True

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        MockHandler.last_payload = payload
        MockHandler.last_auth = self.headers.get("Authorization")
        scenario = payload.get("model", "stream")

        if scenario == "401":
            self._json(401, {"error": {"message": "invalid api key"}})
            return
        if scenario == "429":
            self._json(429, {"error": {"message": "slow down"}}, {"Retry-After": "7"})
            return
        if scenario == "500":
            self._json(500, {"error": {"message": "upstream exploded"}})
            return
        if scenario == "stream":
            self._start_sse()
            self.wfile.write(b": keep-alive\n\n")
            self.wfile.write(b"event: message\n")
            self._event({"choices": [{"delta": {"reasoning_content": "思考"}}]})
            self._event({"choices": [{"delta": {"content": "你"}}]})
            self._event({"choices": [{"delta": {"content": "好"}}]})
            self._event({"choices": [{"delta": {"content": ", "}}]})
            self._event({"choices": [{"delta": {"content": "世界"}}]})
            self._event({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 9}})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if scenario == "drop":
            self._start_sse()
            self._event({"choices": [{"delta": {"content": "半句"}}]})
            self._event({"choices": [{"delta": {"content": "还在"}}]})
            return
        if scenario == "timeout":
            self._start_sse()
            self._event({"choices": [{"delta": {"content": "慢"}}]})
            time.sleep(0.8)
            return
        if scenario == "cancel":
            self._start_sse()
            for i in range(8):
                self._event({"choices": [{"delta": {"content": str(i)}}]})
                time.sleep(0.05)
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                pass
            return
        if scenario == "badjson":
            self._start_sse()
            self._event({"choices": [{"delta": {"content": "ok"}}]})
            self.wfile.write(b"data: {oops}\n\n")
            self.wfile.flush()
            return
        if scenario == "nodone":
            self._start_sse()
            self._event({"choices": [{"delta": {"content": "完整"}}]})
            self._event({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            return
        self._json(404, {"error": {"message": "unknown scenario"}})


@pytest.fixture(scope="module")
def base_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def client(base_url, key="sk-test"):
    return ChatClient(api_key=key, base_url=base_url, timeout=2.0)


def test_stream_collects_content_and_reasoning(base_url):
    deltas = []
    result = client(base_url).stream(
        [{"role": "user", "content": "hi"}],
        model="stream",
        on_delta=lambda kind, text: deltas.append((kind, text)),
    )
    assert result.status == "complete"
    assert result.text == "你好, 世界"
    assert result.reasoning == "思考"
    assert result.finish_reason == "stop"
    assert result.usage == {"total_tokens": 9}
    assert ("content", "你") in deltas
    assert MockHandler.last_auth == "Bearer sk-test"
    assert MockHandler.last_payload["stream"] is True
    assert MockHandler.last_payload["model"] == "stream"


def test_401_is_auth_error(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="401")
    assert result.status == "error"
    assert result.error.kind == "auth"
    assert result.error.status == 401
    assert "invalid api key" in result.error.message


def test_429_carries_retry_after(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="429")
    assert result.error.kind == "rate_limit"
    assert result.error.retry_after == 7.0
    assert result.error.retryable


def test_500_is_server_error(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="500")
    assert result.error.kind == "server"
    assert result.error.status == 500


def test_drop_keeps_partial_text(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="drop")
    assert result.status == "error"
    assert result.error.kind == "connection"
    assert result.text == "半句还在"


def test_timeout_keeps_partial_text(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="timeout", timeout=0.25)
    assert result.status == "error"
    assert result.error.kind == "timeout"
    assert result.text == "慢"


def test_cancel_preserves_what_arrived(base_url):
    cancel = threading.Event()
    seen = []

    def on_delta(kind, text):
        seen.append(text)
        cancel.set()

    started = time.monotonic()
    result = client(base_url).stream(
        [{"role": "user", "content": "x"}], model="cancel", cancel=cancel, on_delta=on_delta
    )
    elapsed = time.monotonic() - started
    assert result.status == "cancelled"
    assert result.text == "0"
    assert seen == ["0"]
    assert elapsed < 1.0


def test_bad_json_is_protocol_error_with_partial(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="badjson")
    assert result.status == "error"
    assert result.error.kind == "protocol"
    assert result.text == "ok"


def test_finish_without_done_is_complete(base_url):
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="nodone")
    assert result.status == "complete"
    assert result.text == "完整"


def test_missing_key_is_config_error(base_url):
    result = client(base_url, key="").stream([{"role": "user", "content": "x"}], model="stream")
    assert result.status == "error"
    assert result.error.kind == "config"


def test_empty_context_is_config_error(base_url):
    result = client(base_url).stream([], model="stream")
    assert result.error.kind == "config"


def test_cancel_before_start(base_url):
    cancel = threading.Event()
    cancel.set()
    result = client(base_url).stream([{"role": "user", "content": "x"}], model="stream", cancel=cancel)
    assert result.status == "cancelled"
    assert result.text == ""


def test_classify_http():
    assert classify_http(401, None, b"{}").kind == "auth"
    assert classify_http(403, None, b"{}").kind == "auth"
    assert classify_http(422, None, b'{"error":{"message":"bad"}}').kind == "bad_request"
    assert classify_http(503, None, b"{}").kind == "server"