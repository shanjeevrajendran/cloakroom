"""Local service that keeps the model loaded. Listens on 127.0.0.1 only and requires the token from
~/.cloakroom/token on every call. Never logs request bodies or values.

POST /mask     {"text", "session", "mode"?} -> {"text", "mode", "masked": [[placeholder, kind, source]], "skipped": [...]}
POST /restore  {"text", "session"}          -> {"text", "unknown": [placeholders not in the vault]}
GET  /health                                 -> {"ok": true, "model": bool}
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from . import config, engine
from .vault import Vault

LOCK = Lock()  # one model instance; calls are serialized
VAULTS: dict[str, Vault] = {}


def _vault(session: str) -> Vault:
    if session not in VAULTS:
        VAULTS[session] = Vault(config.vault_path(session))
    return VAULTS[session]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        given = self.headers.get("X-Cloakroom-Token", "")
        # re-read the token each time: if ~/.cloakroom is reset while the service runs, it follows the new token
        return host in ("127.0.0.1", "localhost") and hmac.compare_digest(given, config.token())

    def do_GET(self):
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        self._send(200, {"ok": True, "model": engine._MODEL is not None})

    def do_POST(self):
        if not self._authorized():
            return self._send(403, {"error": "forbidden"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            vault = _vault(req.get("session", "default"))
            with LOCK:
                if self.path == "/mask":
                    r = engine.mask(req["text"], vault, req.get("mode") or self.server.mode, config.load()["allow"])
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
