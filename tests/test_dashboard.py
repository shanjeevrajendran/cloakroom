"""The local dashboard: sign-in with a one-time code, cookie-gated API, labels, values only on Reveal."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from test_shell_and_hooks import run_hook


@pytest.fixture
def server():
    from cloakroom import daemon
    srv = ThreadingHTTPServer(("127.0.0.1", 0), daemon.Handler)
    srv.mode = "rules"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def call(port, method, path, body=None, headers=None, host="127.0.0.1"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"{host}:{port}", **(headers or {})}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


def sign_in(port):
    from cloakroom import config
    _, _, raw = call(port, "POST", "/dashboard/code", {}, {"X-Cloakroom-Token": config.token()})
    code = json.loads(raw)["code"]
    status, headers, _ = call(port, "GET", f"/dashboard?code={code}")
    assert status == 303 and headers["Location"] == "/dashboard"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    return {"Cookie": cookie.split(";")[0]}, code


def test_needs_sign_in(server):
    assert call(server, "GET", "/dashboard")[0] == 403
    assert call(server, "GET", "/api/summary")[0] == 403
    assert call(server, "POST", "/dashboard/code", {})[0] == 403  # no token
    assert call(server, "GET", "/dashboard?code=made-up")[0] == 403


def test_code_works_once_and_page_loads(server):
    cookie, code = sign_in(server)
    assert call(server, "GET", f"/dashboard?code={code}")[0] == 403  # used up
    status, headers, page = call(server, "GET", "/dashboard", headers=cookie)
    assert status == 200 and b"Cloakroom Dashboard" in page and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert call(server, "GET", "/dashboard", headers=cookie, host="evil.example")[0] == 403  # DNS rebinding


def test_summary_values_and_labels(server, monkeypatch, capsys):
    run_hook("post", {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "/x/.env"},
                      "tool_response": {"file": {"content": "DB_PASSWORD=Xk9vT2pLq8mN3r"}}}, monkeypatch, capsys)
    cookie, _ = sign_in(server)
    summary = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])
    ev = summary["events"][0]
    assert "Xk9vT2pLq8mN3r" not in json.dumps(summary) and ev["kinds"] == {"SECRET": 1} and summary["decisions"] == 0
    vals = json.loads(call(server, "GET", f"/api/values?id={ev['id']}", headers=cookie)[2])["values"]
    assert vals == [{"placeholder": "PII_SECRET_1", "kind": "SECRET", "value": "Xk9vT2pLq8mN3r"}]

    body = {"id": ev["id"], "label": "false_alarm", "placeholders": ["PII_SECRET_1"]}
    assert call(server, "POST", "/api/label", body, cookie)[0] == 403  # no X-Cloakroom header
    assert call(server, "POST", "/api/label", body, {**cookie, "X-Cloakroom": "1"})[0] == 200
    from cloakroom import labels
    assert labels.load()[ev["id"]]["items"] == [{"kind": "SECRET", "value": "Xk9vT2pLq8mN3r"}]
    summary = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])
    assert summary["events"][0]["label"] == "false_alarm" and summary["decisions"] == 1
    assert "Xk9vT2pLq8mN3r" not in json.dumps(summary)

    bad = {"id": ev["id"], "label": "missed", "missed": [{"kind": "PERSON", "value": " "}]}
    assert call(server, "POST", "/api/label", bad, {**cookie, "X-Cloakroom": "1"})[0] == 400
    assert call(server, "POST", "/api/label", {"id": "nope", "label": "correct"}, {**cookie, "X-Cloakroom": "1"})[0] == 400
    assert call(server, "POST", "/api/label", {"id": ev["id"], "label": "none"}, {**cookie, "X-Cloakroom": "1"})[0] == 200
    assert labels.load() == {}
