from __future__ import annotations

import os
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from desktop.local_http import opener


class Handler(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        type(self).hits += 1
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class TransportTests(unittest.TestCase):
    def start_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_non_loopback_rejected(self):
        with self.assertRaises(urllib.error.URLError):
            opener().open("http://8.8.8.8:80/", timeout=0.1)

    def test_proxy_environment_ignored_for_loopback(self):
        server, thread = self.start_server()
        old_http = os.environ.get("HTTP_PROXY")
        old_https = os.environ.get("HTTPS_PROXY")
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        try:
            with opener().open(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
                self.assertEqual(response.read(), b"ok")
        finally:
            if old_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = old_http
            if old_https is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = old_https
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_redirect_not_followed(self):
        Handler.hits = 0
        server, thread = self.start_server()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener().open(f"http://127.0.0.1:{server.server_port}/redirect", timeout=3)
            self.assertEqual(caught.exception.code, 302)
            self.assertEqual(Handler.hits, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
