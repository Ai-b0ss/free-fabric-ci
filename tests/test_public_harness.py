from __future__ import annotations

import json
import math
import os
import socket
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from desktop.combined_acceptance import Surface, _endpoint_identity, _local_base_url, _validate_combined_surfaces, run_soak
from desktop.local_http import opener


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def send_json(self, payload, status=200, extra_headers=None):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)


def start_http(handler_type):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_http(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def nonce_from_text(text: str) -> str:
    prefix = "Call ff_echo with value "
    suffix = ". After its result"
    return text.split(prefix, 1)[1].split(suffix, 1)[0]


class ChatHandler(QuietHandler):
    extra_final = False

    def do_GET(self):
        if self.path == "/v1/models":
            return self.send_json({"object": "list", "data": [{"id": "qwen-test"}]})
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/chat/completions":
            return self.send_error(404)
        tool_messages = [m for m in body.get("messages", []) if isinstance(m, dict) and m.get("role") == "tool"]
        user = body.get("messages", [{}])[0].get("content", "")
        nonce = nonce_from_text(user)
        if not tool_messages:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "ff_echo", "arguments": json.dumps({"value": nonce})}}]}
        else:
            final = nonce + (" extra" if type(self).extra_final else "")
            message = {"role": "assistant", "content": final}
        self.send_json({"choices": [{"message": message}]})


class ResponsesHandler(QuietHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            return self.send_json({"object": "list", "data": [{"id": "notion-test"}]})
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/responses":
            return self.send_error(404)
        items = body.get("input", [])
        user = next((x for x in items if isinstance(x, dict) and x.get("type") == "message"), {})
        nonce = nonce_from_text(user.get("content", ""))
        if not any(isinstance(x, dict) and x.get("type") == "function_call_output" for x in items):
            output = [{"type": "function_call", "name": "ff_echo", "call_id": "call-2", "arguments": json.dumps({"value": nonce})}]
        else:
            output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": nonce}]}]
        self.send_json({"output": output})


class RedirectHandler(QuietHandler):
    target_hits = 0

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/target":
            type(self).target_hits += 1
            return self.send_json({"ok": True})
        self.send_error(404)


class SimpleHandler(QuietHandler):
    def do_GET(self):
        self.send_json({"ok": True})


class PublicHarnessTests(unittest.TestCase):
    def test_non_loopback_rejected(self):
        with self.assertRaises(RuntimeError):
            _local_base_url("http://8.8.8.8:80")

    def test_localhost_alias_matches_127(self):
        self.assertEqual(_endpoint_identity("http://localhost:8000"), _endpoint_identity("http://127.0.0.1:8000"))

    def test_same_endpoint_cannot_impersonate_two_services(self):
        surfaces = [Surface("qwen", "http://localhost:8000", frozenset({"q"}), "openai-chat"), Surface("notion", "http://127.0.0.1:8000", frozenset({"n"}), "responses")]
        with self.assertRaisesRegex(ValueError, "endpoints must be unique"):
            _validate_combined_surfaces(surfaces, require_tools=True)

    def test_non_finite_timing_rejected(self):
        surfaces = [Surface("qwen", "http://127.0.0.1:8000", frozenset({"q"}), "openai-chat"), Surface("notion", "http://127.0.0.1:8001", frozenset({"n"}), "responses")]
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "invalid timing"):
                run_soak(surfaces, duration=value, interval=1, timeout=1, require_tools=True)

    def test_proxy_environment_is_ignored(self):
        server, thread = start_http(SimpleHandler)
        old_http = os.environ.get("HTTP_PROXY")
        old_https = os.environ.get("HTTPS_PROXY")
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        try:
            with opener().open(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
                self.assertEqual(response.status, 200)
        finally:
            if old_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = old_http
            if old_https is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = old_https
            stop_http(server, thread)

    def test_redirect_is_not_followed(self):
        RedirectHandler.target_hits = 0
        server, thread = start_http(RedirectHandler)
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener().open(f"http://127.0.0.1:{server.server_port}/redirect", timeout=3)
            self.assertEqual(caught.exception.code, 302)
            self.assertEqual(RedirectHandler.target_hits, 0)
        finally:
            stop_http(server, thread)

    def test_malformed_chunked_body_is_bounded(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve_once():
            conn, _ = listener.accept()
            try:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n5\r\nabc")
            finally:
                conn.close()
                listener.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        with opener().open(f"http://127.0.0.1:{port}/", timeout=3) as response:
            with self.assertRaises(urllib.error.URLError):
                response.read()
        thread.join(timeout=5)

    def test_real_two_surface_tool_roundtrip(self):
        chat, chat_thread = start_http(ChatHandler)
        responses, responses_thread = start_http(ResponsesHandler)
        ChatHandler.extra_final = False
        try:
            surfaces = [Surface("qwen", f"http://127.0.0.1:{chat.server_port}", frozenset({"qwen-test"}), "openai-chat"), Surface("notion", f"http://127.0.0.1:{responses.server_port}", frozenset({"notion-test"}), "responses")]
            result = run_soak(surfaces, duration=0, interval=0.01, timeout=3, require_tools=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["tool_roundtrips"], ["notion", "qwen"])
        finally:
            stop_http(chat, chat_thread)
            stop_http(responses, responses_thread)

    def test_extra_text_after_tool_result_fails_closed(self):
        chat, chat_thread = start_http(ChatHandler)
        responses, responses_thread = start_http(ResponsesHandler)
        ChatHandler.extra_final = True
        try:
            surfaces = [Surface("qwen", f"http://127.0.0.1:{chat.server_port}", frozenset({"qwen-test"}), "openai-chat"), Surface("notion", f"http://127.0.0.1:{responses.server_port}", frozenset({"notion-test"}), "responses")]
            result = run_soak(surfaces, duration=0, interval=0.01, timeout=3, require_tools=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["failure_count"], 1)
            self.assertIn("tool_result_marker_invalid", result["failures"][0]["error"])
        finally:
            ChatHandler.extra_final = False
            stop_http(chat, chat_thread)
            stop_http(responses, responses_thread)


if __name__ == "__main__":
    unittest.main()
