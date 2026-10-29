"""
HTTP input source: the Phase 8 button test client, and the v0.2 "phone over local network" path.

    GET  /                 button page (static HTML)
    GET  /pair             pairing QR code - only served to this computer itself (loopback)
    POST /api/message      client message envelope (see messages.py), JSON in and out

Every /api request must carry the pairing token (header X-PirateBox-Token). By default the
server binds to 127.0.0.1 only; --lan exposes it to the local network. Standard library only.
"""

import hmac
import json
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import pairing
from .base import InputSource
from .messages import handle_message

log = logging.getLogger("piratebox.http")

MAX_BODY = 4096
PAGE = Path(__file__).resolve().parent / "web" / "index.html"


class HttpInput(InputSource):
    name = "http"

    def __init__(self, engine, host="127.0.0.1", port=7142, token=None, allow_origin=None, ble_name=None):
        super().__init__(engine)
        self.ble_name = ble_name  # advertised Bluetooth name, added to the pairing QR when BLE is on
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(12)
        self.allow_origin = allow_origin  # dev only: lets e.g. the Expo web preview call the API
        self._server = None

    @property
    def bound_port(self):
        return self._server.server_port if self._server else self.port

    def pairing_uri(self):
        """URI for the phone app's QR scanner; None when not reachable from the network."""
        if self.host in ("0.0.0.0", ""):
            ip = _lan_ip()
            return pairing.pairing_uri(ip, self.bound_port, self.token, ble=self.ble_name) if ip else None
        return None

    def urls(self):
        hosts = [self.host] if self.host not in ("0.0.0.0", "") else ["127.0.0.1", _lan_ip()]
        return [f"http://{h}:{self.bound_port}/#token={self.token}" for h in hosts if h]

    def start(self):
        """Serve on a background thread (so it can run alongside the console)."""
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler_class())
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="http-input", daemon=True).start()

    def run(self):
        self.start()
        threading.Event().wait()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def _handler_class(self):
        source = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PirateBox"

            def log_message(self, fmt, *args):
                log.debug("%s " + fmt, self.client_address[0], *args)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/pair":
                    return self._pair_page()
                if path != "/":
                    return self._send(404, {"success": False, "status": "NOT_FOUND"})
                self._send_html(PAGE.read_bytes())

            def _pair_page(self):
                # The QR carries the token, so only the computer's own screen may show it.
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._send(403, {"success": False, "status": "FORBIDDEN"})
                uri = source.pairing_uri()
                if uri is None:
                    body = "<p>Start PirateBox with --lan to pair a phone.</p>"
                else:
                    body = pairing.qr_svg(uri)
                self._send_html((
                    "<!doctype html><meta charset=utf-8><title>Pair phone</title>"
                    "<body style='margin:0;min-height:100vh;display:grid;place-items:center;background:#0e0f11;"
                    "color:#e8eaed;font:16px system-ui'><div style='text-align:center'>"
                    f"<div style='width:320px;margin:auto'>{body}</div>"
                    "<p style='letter-spacing:.12em;text-transform:uppercase;color:#8b919a'>"
                    "Scan with the PirateBox app</p></div>"
                ).encode())

            def _send_html(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                if self.path != "/api/message" or not source.allow_origin:
                    return self._send(404, {"success": False, "status": "NOT_FOUND"})
                self.send_response(204)
                self._cors_headers()
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-PirateBox-Token")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _cors_headers(self):
                if source.allow_origin and self.headers.get("Origin") == source.allow_origin:
                    self.send_header("Access-Control-Allow-Origin", source.allow_origin)
                    self.send_header("Vary", "Origin")

            def do_POST(self):
                if self.path != "/api/message":
                    return self._send(404, {"success": False, "status": "NOT_FOUND"})
                supplied = self.headers.get("X-PirateBox-Token", "")
                if not hmac.compare_digest(supplied.encode(), source.token.encode()):
                    log.warning("rejected request from %s: bad token", self.client_address[0])
                    return self._send(401, {"success": False, "status": "UNAUTHORIZED",
                                            "message": "missing or wrong pairing token"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= MAX_BODY:
                    return self._send(413, {"success": False, "status": "INVALID_MESSAGE",
                                            "message": f"body must be 1..{MAX_BODY} bytes"})
                try:
                    msg = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._send(400, {"success": False, "status": "INVALID_MESSAGE", "message": "invalid JSON"})
                self._send(200, handle_message(source.engine, msg, source=f"http:{self.client_address[0]}"))

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        return Handler


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET address: picks the outbound interface, sends nothing
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
