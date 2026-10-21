"""Local service that keeps the model loaded. Listens on 127.0.0.1 only and requires the token from
~/.cloakroom/token on every call. Never logs request bodies or values.

POST /mask     {"text", "session", "mode"?, "ocr"?} -> {"text", "mode", "masked": [[placeholder, kind, source]], "skipped": [...]}
POST /restore  {"text", "session"}          -> {"text", "unknown": [placeholders not in the vault]}
GET  /health                                 -> {"ok": true, "service": "cloakroom", "model": bool}
POST /dashboard/code                         -> {"code"}: one-time sign-in code for `cloakroom dashboard`
GET  /dashboard, /api/summary, /api/values?id=, POST /api/label, /api/rule, /api/learning: the local dashboard (dashboard.py), cookie-gated
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from . import __version__, config, dashboard, engine
from .vault import Vault

LOCK = Lock()  # one model instance; calls are serialized
VAULTS: dict[str, Vault] = {}


def _vault(session: str) -> Vault:
    if session not in VAULTS:
        VAULTS[session] = Vault(config.vault_path(session))
    return VAULTS[session]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _local_host(self) -> bool:  # also stops DNS rebinding: a foreign name pointed at 127.0.0.1 is refused
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _authorized(self) -> bool:
        given = self.headers.get("X-Cloakroom-Token", "")
        # re-read the token each time: if ~/.cloakroom is reset while the service runs, it follows the new token
        return self._local_host() and hmac.compare_digest(given, config.token())

    def _signed_in(self) -> bool:
        return self._local_host() and dashboard.signed_in(self.headers.get("Cookie"))

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            return self._send(200, {"ok": True, "service": "cloakroom", "version": __version__,
                                    "model": engine._MODEL is not None})
        if url.path == "/dashboard":
            code = parse_qs(url.query).get("code", [""])[0]
            if code and self._local_host() and dashboard.redeem(code):
                return self._send(303, b"", "text/plain", [("Location", "/dashboard"), ("Set-Cookie", dashboard.set_cookie())])
            if not self._signed_in():
                return self._send(403, b"Cloakroom dashboard: run `cloakroom dashboard` to open it signed in.\n",
                                  "text/plain; charset=utf-8")
            return self._send(200, dashboard.page(), "text/html; charset=utf-8",
                              [("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; "
                                "style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")])
        if url.path.startswith("/api/"):
            if not self._signed_in():
                return self._send(403, {"error": "forbidden"})
            if url.path == "/api/summary":
                return self._send(200, dashboard.summary())
            if url.path == "/api/values":
                return self._send(200, {"values": dashboard.values(parse_qs(url.query).get("id", [""])[0])})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/api/label", "/api/rule", "/api/learning"):
            # cookie + a custom header: another site's page can't send it without a preflight we never answer
            if not (self._signed_in() and self.headers.get("X-Cloakroom") == "1"):
                return self._send(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                handler = {"/api/label": dashboard.label, "/api/rule": dashboard.rule, "/api/learning": dashboard.set_learning}
                return self._send(200, handler[self.path](body))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if not self._authorized():
            return self._send(403, {"error": "forbidden"})
        if self.path == "/dashboard/code":
            return self._send(200, {"code": dashboard.new_code()})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            vault = _vault(req.get("session", "default"))
            with LOCK:
                if self.path == "/mask":
                    cfg = config.load()
                    r = engine.mask(req["text"], vault, req.get("mode") or self.server.mode, cfg["allow"], cfg["always"],
                                    bool(req.get("ocr")))
                    return self._send(200, {"text": r.text, "mode": r.mode, "masked": r.masked, "skipped": r.skipped})
                if self.path == "/restore":
                    text, unknown = vault.restore(req["text"])
                    return self._send(200, {"text": text, "unknown": unknown})
            self._send(404, {"error": "not found"})
        except engine.ModelUnavailable as e:
            self._send(503, {"error": "model_unavailable", "detail": str(e)})
        except Exception as e:  # callers fail closed on any error
            self._send(500, {"error": type(e).__name__})

    def log_message(self, *args):
        pass


def serve():
    cfg = config.load()
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), Handler)
    server.mode = cfg["mode"]
    if cfg["mode"] == "full":
        engine._model()  # load once at start; raises ModelUnavailable if extras are missing
        engine.mask("warm up")
    server.serve_forever()
