"""Another program on Cloakroom's port (e.g. a proxy that answers 404) must give a clear message, not a bare HTTP 404."""
import http.server
import json
import threading

import pytest

from cloakroom import client, config


class Other(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"service": "some-proxy", "status": "healthy"})

    def do_POST(self):
        self._send(404, {"detail": "Not Found"})

    def log_message(self, *a):
        pass


@pytest.fixture
def other_port(tmp_path, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", str(srv.server_address[1]))
    yield srv.server_address[1]
    srv.shutdown()


def test_default_port_is_not_headrooms():
    assert config.DEFAULTS["port"] != 8787


def test_foreign_service_on_port_gives_clear_message(other_port):
    assert client.is_cloakroom(other_port) is False
    with pytest.raises(client.Unavailable) as e:
        client.mask("Jane Example", "s1")
    assert "used by another program" in str(e.value) and str(other_port) in str(e.value)


def test_nothing_on_port_counts_as_ours():
    assert client.health(1) is None and client.is_cloakroom(1)
