from __future__ import annotations

import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from desktop.local_http import opener


class HitHandler(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class LocalHttpUrlGuardTests(unittest.TestCase):
    def test_userinfo_is_rejected_before_connect(self):
        HitHandler.hits = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), HitHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://user:pass@127.0.0.1:{server.server_port}/"
            with self.assertRaisesRegex(urllib.error.URLError, "loopback_url_invalid"):
                opener().open(url, timeout=2)
            self.assertEqual(HitHandler.hits, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_invalid_port_is_normalized_to_bounded_url_error(self):
        with self.assertRaisesRegex(urllib.error.URLError, "loopback_url_invalid"):
            opener().open("http://127.0.0.1:99999/", timeout=2)

    def test_plain_loopback_still_connects(self):
        HitHandler.hits = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), HitHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with opener().open(f"http://127.0.0.1:{server.server_port}/", timeout=2) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(HitHandler.hits, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
