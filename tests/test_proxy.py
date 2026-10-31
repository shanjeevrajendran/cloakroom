"""1.0: `cloakroom proxy` (proxy.py): masks the text in a request body, forwards everything else untouched, streams the
reply through, writes the ledger, and its placeholders restore in the hooks. A fake upstream stands in for the AI
company. Synthetic values only."""
import gzip
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

KEY = "sk-" + "proj9Hq2LmNp4RvX7yBc1Df5Gh6Jk0Q4z"  # an API-key shape, not a real one
EMAIL = "henrik.dahl@quillfield.com"
AUTH = "sk-ant-" + "api03-Zk8mQ2vL7xNp4RtY1bCd5Fg"  # a header value: must reach the upstream exactly as sent
SSE = [b"event: message_start\ndata: {\"a\":1}\n\n", b"event: delta\ndata: {\"t\":\"hel\"}\n\n",
       b"event: delta\ndata: {\"t\":\"lo\"}\n\n", b"event: stop\ndata: {}\n\n"]


class Upstream:
    """Records what it was sent; answers JSON, or a slow SSE stream on /v1/stream."""

    def __init__(self):
        self.seen = []
        self.first_chunk_received = threading.Event()
        up = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle_any(self):
                n = int(self.headers.get("Content-Length") or 0)
                up.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers.items()),
                                "body": self.rfile.read(n) if n else b""})
                if self.path.startswith("/v1/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i, part in enumerate(SSE):
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                        self.wfile.flush()
                        if i == 0:
                            up.first_chunk_received.wait(5)  # the test sets it once the client has chunk one
                    self.wfile.write(b"0\r\n\r\n")
                    self.close_connection = True
                    return
                reply = json.dumps({"ok": True, "echo": EMAIL}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Upstream", "yes")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            do_GET = do_POST = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def stack():
    from cloakroom import config, proxy
    up = Upstream()
    cfg = config.HOME
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(json.dumps({"proxy_upstreams": {"/fake": f"http://127.0.0.1:{up.port}"}}))
    server = proxy.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    up.proxy_port = server.server_address[1]
    yield up
    server.shutdown()
    up.server.shutdown()


def send(up, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", up.proxy_port, timeout=10)
    h = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    if host:
        h["Host"] = host
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def sent_json(up):
    return json.loads(up.seen[-1]["body"])


def rows(kind=None):
    from cloakroom import events
    return [e for e in events.all_events() if kind is None or e["event"] == kind]


# --- the text in each vendor's body shape ---

def test_anthropic_shape_masks_text_and_leaves_structure(stack):
    body = {"model": "claude-x", "max_tokens": 5, "metadata": {"user_id": "u-1"},
            "system": [{"type": "text", "text": f"Deploy key is {KEY}"}],
            "messages": [{"role": "user", "content": f"mail {EMAIL} please"},
                         {"role": "assistant", "content": [
                             {"type": "thinking", "thinking": "plan the call", "signature": "abc"},
                             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"echo {KEY}"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                       "content": [{"type": "text", "text": f"out {KEY}"}]},
                                                      {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                                   "data": "iVBORw0KGgo="}}]}],
            "tools": [{"name": "Bash", "description": f"Run it. Ask {EMAIL}",
                       "input_schema": {"type": "object", "properties": {"command": {"type": "string", "description": "the command"}},
                                        "required": ["command"]}}]}
    status, _, _ = send(stack, "POST", "/fake/v1/messages", body, {"x-api-key": AUTH})
    assert status == 200
    raw = stack.seen[-1]["body"].decode()
    assert KEY not in raw and EMAIL not in raw
    got = json.loads(raw)
    assert got["model"] == "claude-x" and got["metadata"] == {"user_id": "u-1"}
    assert got["tools"][0]["name"] == "Bash" and got["tools"][0]["input_schema"]["required"] == ["command"]
    assert "PII_EMAIL_" in got["tools"][0]["description"]
    assert got["messages"][1]["content"][0] == body["messages"][1]["content"][0]  # signed block: untouched
    assert got["messages"][1]["content"][1]["name"] == "Bash" and "PII_SECRET_" in got["messages"][1]["content"][1]["input"]["command"]
    assert got["messages"][2]["content"][1] == body["messages"][2]["content"][1]  # the image
    assert "PII_SECRET_" in got["system"][0]["text"] and "PII_SECRET_" in got["messages"][2]["content"][0]["content"][0]["text"]


def test_a_schema_property_named_type_does_not_break_the_walk(stack):
    # found live: Claude Code's tool schemas have properties called "type" whose value is a dict
    body = {"messages": [{"role": "user", "content": f"k {KEY}"}],
            "tools": [{"name": "t", "description": "d", "input_schema": {"properties": {"type": {"type": "string", "description": "kind"}}}}]}
    assert send(stack, "POST", "/fake/v1/messages", body)[0] == 200
    assert KEY not in stack.seen[-1]["body"].decode() and sent_json(stack)["tools"] == body["tools"]


def test_any_failure_while_checking_is_refused_not_dropped(stack, monkeypatch):
    from cloakroom import proxy
    monkeypatch.setattr(proxy, "_walk", lambda *a, **k: 1 / 0)
    status, _, body = send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": "hello there"}]})
    assert status == 503 and stack.seen == [] and rows("proxy")[-1]["action"] == "deny"


def test_openai_chat_and_responses_shapes(stack):
    chat = {"model": "gpt-x", "messages": [{"role": "system", "content": f"key {KEY}"},
                                           {"role": "user", "content": [{"type": "text", "text": f"to {EMAIL}"},
                                                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
                                           {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {"name": "send_mail", "arguments": json.dumps({"to": EMAIL})}}]}],
            "tools": [{"type": "function", "function": {"name": "send_mail", "description": f"mail {EMAIL}", "parameters": {}}}]}
    send(stack, "POST", "/fake/v1/chat/completions", chat)
    s = stack.seen[-1]["body"].decode()
    assert KEY not in s and EMAIL not in s
    got = json.loads(s)
    assert got["messages"][2]["tool_calls"][0]["function"]["name"] == "send_mail" and got["tools"][0]["function"]["name"] == "send_mail"
    assert got["messages"][1]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    resp = {"model": "gpt-x", "instructions": f"key {KEY}", "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"hi {EMAIL}"}]},
        {"type": "function_call", "call_id": "c1", "name": "run", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": f"printed {KEY}"}]}
    send(stack, "POST", "/fake/v1/responses", resp)
    s = stack.seen[-1]["body"].decode()
    assert KEY not in s and EMAIL not in s and json.loads(s)["input"][1]["name"] == "run"


def test_gemini_shape(stack):
    body = {"systemInstruction": {"parts": [{"text": f"key {KEY}"}]},
            "contents": [{"role": "user", "parts": [{"text": f"mail {EMAIL}"},
                                                    {"inlineData": {"mimeType": "image/png", "data": "AAAA"}}]},
                         {"role": "model", "parts": [{"functionCall": {"name": "lookup", "args": {"who": EMAIL}}}]},
                         {"role": "user", "parts": [{"functionResponse": {"name": "lookup", "response": {"out": KEY}}}]}],
            "tools": [{"functionDeclarations": [{"name": "lookup", "description": f"find {EMAIL}", "parameters": {"type": "OBJECT"}}]}],
            "generationConfig": {"temperature": 0}}
    send(stack, "POST", "/fake/v1beta/models/g:generateContent?key=abc", body)
    seen = stack.seen[-1]
    assert seen["path"] == "/v1beta/models/g:generateContent?key=abc"
    s = seen["body"].decode()
    assert KEY not in s and EMAIL not in s
    got = json.loads(s)
    assert got["contents"][1]["parts"][0]["functionCall"]["name"] == "lookup"
    assert got["tools"][0]["functionDeclarations"][0]["name"] == "lookup"
    assert got["contents"][0]["parts"][1]["inlineData"] == {"mimeType": "image/png", "data": "AAAA"}


# --- forwarding ---

def test_headers_method_path_and_query_forwarded_untouched_and_never_logged(stack):
    send(stack, "POST", "/fake/v1/messages?beta=true", {"messages": [{"role": "user", "content": f"k {KEY}"}]},
         {"x-api-key": AUTH, "Authorization": f"Bearer {AUTH}", "anthropic-version": "2023-06-01", "X-Odd": EMAIL})
    seen = stack.seen[-1]
    assert seen["method"] == "POST" and seen["path"] == "/v1/messages?beta=true"
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert h["x-api-key"] == AUTH and h["authorization"] == f"Bearer {AUTH}" and h["anthropic-version"] == "2023-06-01"
    assert h["x-odd"] == EMAIL  # not scanned, not masked
    assert int(h["content-length"]) == len(seen["body"])
    from cloakroom import config
    blob = b"".join(p.read_bytes() for p in config.HOME.rglob("*") if p.is_file())
    assert AUTH.encode() not in blob and KEY.encode() not in blob and EMAIL.encode() not in blob


def test_get_without_a_body_and_reply_come_back_unchanged(stack):
    status, headers, body = send(stack, "GET", "/fake/v1/models?limit=5")
    assert status == 200 and stack.seen[-1]["path"] == "/v1/models?limit=5" and stack.seen[-1]["body"] == b""
    assert headers["X-Upstream"] == "yes" and json.loads(body)["echo"] == EMAIL  # replies are not masked


def test_streaming_is_passed_chunk_by_chunk_byte_for_byte(stack):
    c = http.client.HTTPConnection("127.0.0.1", stack.proxy_port, timeout=10)
    c.request("POST", "/fake/v1/stream", body=json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    first = r.read1(4096)
    assert first == SSE[0]  # arrived while the upstream is still holding the rest back: nothing is buffered
    stack.first_chunk_received.set()
    rest = b""
    while True:
        part = r.read1(4096)
        if not part:
            break
        rest += part
    assert first + rest == b"".join(SSE)


def test_gzip_body_is_masked_and_sent_plain(stack):
    raw = gzip.compress(json.dumps({"messages": [{"role": "user", "content": f"k {KEY}"}]}).encode())
    send(stack, "POST", "/fake/v1/messages", raw, {"Content-Encoding": "gzip", "Content-Type": "application/json"})
    seen = stack.seen[-1]
    assert KEY.encode() not in seen["body"] and b"PII_SECRET_" in seen["body"]
    assert "content-encoding" not in {k.lower() for k in seen["headers"]}


def test_unreadable_bodies_are_refused_and_never_forwarded(stack):
    cases = [(b"\x00\x01 not json " + KEY.encode(), {}, "not-json", b"not JSON"),
             (b"zzzz" + KEY.encode(), {"Content-Encoding": "zstd"}, "encoding", b"zstd"),
             (b"zzzz" + KEY.encode(), {"Content-Encoding": "br"}, "encoding", b"brotli")]
    for raw, h, why, said in cases:
        status, _, body = send(stack, "POST", "/fake/v1/messages", raw, h)
        assert status == 503 and said in body and b"docs-LIMITS.md" in body and KEY.encode() not in body
        assert stack.seen == []  # the upstream received nothing
        ev = rows("proxy")[-1]
        assert ev["action"] == "deny" and ev["reason"] == why and ev["check"] == "proxy request"
    from cloakroom import config
    (config.HOME / "config.json").write_text(json.dumps({"proxy_max_mb": 0.0001, "proxy_upstreams": {"/fake": f"http://127.0.0.1:{stack.port}"}}))
    big = json.dumps({"messages": [{"role": "user", "content": "x " * 400}]}).encode()
    status, _, body = send(stack, "POST", "/fake/v1/messages", big)
    assert status == 503 and b"proxy_max_mb" in body and stack.seen == [] and rows("proxy")[-1]["reason"] == "too-large"
    from cloakroom import report
    assert not report.skipped()  # refused, not "not checked"
    assert any(r["session"] == "proxy" and r["action"] == "denied" for r in report.rows())  # `cloakroom report` shows it


def test_zstd_body_is_refused_with_the_secret_inside(stack):
    # what Codex signed in with ChatGPT sends: a compressed JSON body holding a key
    raw = b"\x28\xb5\x2f\xfd" + json.dumps({"input": [{"role": "user", "content": KEY}]}).encode()
    status, _, _ = send(stack, "POST", "/fake/v1/responses", raw, {"Content-Encoding": "zstd"})
    assert status == 503 and stack.seen == []


def test_empty_bodies_and_gets_still_pass(stack):
    for method in ("GET", "POST"):
        status, _, _ = send(stack, method, "/fake/v1/models", None)
        assert status == 200
    assert [s["method"] for s in stack.seen] == ["GET", "POST"] and all(s["body"] == b"" for s in stack.seen)


def test_plain_json_is_still_masked_and_forwarded(stack):
    status, _, _ = send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": f"k {KEY}"}]})
    assert status == 200 and KEY.encode() not in stack.seen[-1]["body"] and b"PII_SECRET_" in stack.seen[-1]["body"]


def test_a_body_with_nothing_to_mask_is_forwarded_byte_for_byte(stack):
    raw = b'{ "messages" : [ {"role":"user","content":"hello there"} ] }'
    send(stack, "POST", "/fake/v1/messages", raw)
    assert stack.seen[-1]["body"] == raw


def test_only_this_machine_and_only_known_routes(stack):
    assert send(stack, "GET", "/fake/v1/models", host="evil.example")[0] == 403
    assert send(stack, "GET", "/nowhere/v1/models")[0] == 404
    assert json.loads(send(stack, "GET", "/health")[2])["service"] == "cloakroom-proxy"
    assert stack.seen == []
    from cloakroom import proxy
    assert proxy.make_server(0).server_address[0] == "127.0.0.1"


def test_it_fails_closed_when_the_check_cannot_run(stack, monkeypatch):
    from cloakroom import client
    monkeypatch.setattr(client, "mask", lambda *a, **k: (_ for _ in ()).throw(client.Unavailable("down")))
    status, _, body = send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": "hello there"}]})
    assert status == 503 and b"cloakroom doctor" in body and stack.seen == []


# --- the ledger and the approved-sites list ---

def test_ledger_row_has_host_and_kinds_only(stack):
    send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": f"{KEY} and {EMAIL}"}]})
    from cloakroom import events, report
    ev = [e for e in rows("proxy")][-1]
    assert ev["dest"] == ["127.0.0.1"] and ev["action"] == "masked" and set(ev["carries"]) >= {"SECRET", "EMAIL"}
    assert KEY not in json.dumps(events.read(1000)) and EMAIL not in json.dumps(events.read(1000))
    dest = {d["host"]: d for d in report.destinations()}["127.0.0.1"]
    assert dest["calls"] >= 1 and "SECRET" in dest["kinds"]
    assert "127.0.0.1" in report.destinations_text("csv")


def test_approved_sites_list_holds_a_proxy_upstream_like_any_other(stack, monkeypatch):
    from cloakroom import config, proxy
    (config.HOME / "config.json").write_text(json.dumps({"egress": "allowlist", "proxy_upstreams": {"/held": "https://api.held.example"}}))
    status, _, body = send(stack, "POST", "/held/v1/messages", {"messages": [{"role": "user", "content": "hello"}]})
    assert status == 403 and b"cloakroom allow-host api.held.example" in body
    ev = rows("proxy")[-1]
    assert ev["action"] == "held" and ev["dest"] == ["api.held.example"] and ev["hold"] == "approved-sites"
    config.set_allowed_hosts(["api.held.example"])
    forwarded = []
    monkeypatch.setattr(proxy.Handler, "_forward", lambda self, *a: (forwarded.append(a), self._reply(200, b"{}"))[0])
    assert send(stack, "POST", "/held/v1/messages", {"messages": [{"role": "user", "content": "hello"}]})[0] == 200
    assert forwarded and rows("proxy")[-1]["action"] == "pass"


# --- placeholders round trip through the hooks ---

def hook(event, data, project, session="s1"):
    from cloakroom import hook as h
    return h.handle(event, {"session_id": session, "cwd": str(project), **data})


def test_a_value_the_proxy_masked_is_restored_when_a_hook_writes_it(stack, tmp_path):
    import re
    send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": f"deploy key {KEY}"}]})
    ph = re.search(r"PII_SECRET_\d+_[a-z2-7]{6}", stack.seen[-1]["body"].decode()).group()
    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    out = hook("pre", {"tool_name": "Write", "tool_use_id": "w1",
                       "tool_input": {"file_path": str(project / ".env"), "content": f"KEY={ph}\n"}}, project, "other-session")
    (project / ".env").write_text(out["hookSpecificOutput"]["updatedInput"]["content"])  # what Write would do
    assert (project / ".env").read_text() == f"KEY={KEY}\n"
    out = hook("pre", {"tool_name": "Bash", "tool_use_id": "b1", "tool_input": {"command": f"echo {ph} > k.txt"}}, project, "third")
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == f"echo {KEY} > k.txt"
    out = hook("post", {"tool_name": "Bash", "tool_use_id": "b2", "tool_input": {"command": "cat k.txt"},
                        "tool_response": {"stdout": f"key: {KEY}\n"}}, project, "third")
    assert KEY not in json.dumps(out)
    from cloakroom import config
    assert not [p for p in config.HOME.rglob("*") if p.is_file() and KEY.encode() in p.read_bytes()]  # stored encrypted


def test_a_placeholder_a_hook_issued_passes_through_untouched(stack):
    from cloakroom import client, handoff
    ph = client.mask(f"key {KEY}", "s1")["text"].split()[-1]
    assert ph.startswith("PII_SECRET_")
    send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": f"use {ph} for deploy"}]})
    assert sent_json(stack)["messages"][0]["content"] == f"use {ph} for deploy"
    assert handoff.proxy_values() == {}  # the proxy minted nothing
    assert rows("proxy")[-1]["action"] == "pass" and rows("proxy")[-1]["carries"] == {"SECRET": 1}


def test_same_value_same_placeholder_and_it_expires_after_a_day(stack, monkeypatch):
    import re
    for _ in range(2):
        send(stack, "POST", "/fake/v1/messages", {"messages": [{"role": "user", "content": f"deploy key {KEY}"}]})
    a, b = (re.search(r"PII_SECRET_\d+_[a-z2-7]{6}", s["body"].decode()).group() for s in stack.seen[-2:])
    assert a == b
    from cloakroom import handoff
    assert handoff.proxy_values() == {a: KEY}
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 25 * 3600)
    assert handoff.proxy_values() == {}  # a day on, the hooks no longer restore it


# --- pointing a tool at the proxy ---

def test_install_print_writes_nothing_and_names_the_address(capsys, monkeypatch, tmp_path):
    from cloakroom import cli, proxy
    for var, sub in (("CLAUDE_CONFIG_DIR", "c"), ("CODEX_HOME", "x"), ("GEMINI_CLI_HOME", "g")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    p = proxy.port()
    for agent, want in (("claude", f"http://127.0.0.1:{p}/anthropic"), ("codex", f"http://127.0.0.1:{p}/openai/v1"),
                        ("gemini", f"http://127.0.0.1:{p}/gemini")):
        assert cli.main(["proxy", "install", agent, "--print"]) == 0
        assert want in capsys.readouterr().out
    assert not [p for d in "cxg" for p in (tmp_path / d).rglob("*")]  # no agent file written


def test_claude_install_writes_env_and_uninstall_restores(monkeypatch, tmp_path, capsys):
    from cloakroom import cli
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "settings.json"
    original = {"model": "x", "env": {"ANTHROPIC_BASE_URL": "https://gateway.example", "OTHER": "1"}, "hooks": {}}
    path.write_text(json.dumps(original))
    assert cli.main(["proxy", "install", "claude"]) == 0
    now = json.loads(path.read_text())
    assert now["env"]["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and now["env"]["OTHER"] == "1" and now["model"] == "x"
    from cloakroom import proxy
    assert proxy.pointing()["claude"] == now["env"]["ANTHROPIC_BASE_URL"]
    assert cli.main(["proxy", "uninstall", "claude"]) == 0
    assert json.loads(path.read_text()) == original and "claude" not in proxy.pointing()
    path.unlink()  # no settings file at all: the install makes one and the uninstall removes it again
    assert cli.main(["proxy", "install", "claude"]) == 0 and path.exists()
    assert cli.main(["proxy", "uninstall", "claude"]) == 0 and not path.exists()


def test_claude_uninstall_leaves_an_address_the_user_changed(monkeypatch, tmp_path):
    from cloakroom import cli
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    cli.main(["proxy", "install", "claude"])
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://mine.example"}}))
    cli.main(["proxy", "uninstall", "claude"])
    assert json.loads(path.read_text()) == {"env": {"ANTHROPIC_BASE_URL": "https://mine.example"}}


@pytest.mark.parametrize("text", [
    'openai_base_url = "https://gateway.example/v1"\nmodel = "x"\n\n[profiles.a]\nk = 1\n',  # replaces the old line
    'model = "x"\n\n[profiles.a]\nopenai_base_url = "https://not-top-level"\n',  # a table's key is not the top-level one
    '# mine\r\nmodel = "x"\r\n',  # Windows line endings survive
    None])  # no file
def test_codex_toml_line_is_added_and_the_file_comes_back_byte_for_byte(monkeypatch, tmp_path, text):
    from cloakroom import cli, proxy
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "config.toml"
    if text is not None:
        path.write_bytes(text.encode())
    assert cli.main(["proxy", "install", "codex"]) == 0
    got = path.read_bytes().decode()
    assert f'openai_base_url = "http://127.0.0.1:{proxy.port()}/openai/v1"' in got and got.count("# cloakroom proxy") == 1
    top = got.split("[")[0]
    assert "openai_base_url" in top  # at the top, before any table
    assert cli.main(["proxy", "install", "codex"]) == 0 and path.read_bytes().decode().count("# cloakroom proxy") == 1
    assert cli.main(["proxy", "uninstall", "codex"]) == 0
    if text is None:
        assert not path.exists()
    else:
        assert path.read_bytes().decode() == text


def test_gemini_env_line_is_added_and_the_file_comes_back_byte_for_byte(monkeypatch, tmp_path):
    from cloakroom import cli, proxy
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    path = tmp_path / ".gemini" / ".env"
    path.parent.mkdir()
    path.write_text("GEMINI_API_KEY=abc\nGOOGLE_GEMINI_BASE_URL=https://old.example")  # no trailing newline
    assert cli.main(["proxy", "install", "gemini"]) == 0
    got = path.read_text()
    assert f"GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:{proxy.port()}/gemini" in got and "GEMINI_API_KEY=abc" in got
    assert "old.example" not in got
    assert cli.main(["proxy", "uninstall", "gemini"]) == 0
    assert path.read_text() == "GEMINI_API_KEY=abc\nGOOGLE_GEMINI_BASE_URL=https://old.example"


def test_doctor_is_silent_until_the_proxy_is_in_use(monkeypatch, tmp_path, capsys):
    from cloakroom import cli
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    cli.main(["doctor"])
    assert "proxy:" not in capsys.readouterr().out
    cli.main(["proxy", "install", "claude"])
    capsys.readouterr()
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "FAIL  proxy: not running, but these point at it" in out and "Claude Code" in out



# --- the install warning for a ChatGPT sign-in ---

@pytest.mark.parametrize("mode,warns", [("chatgpt", True), ("apikey", False), (None, False)])
def test_codex_install_warns_on_a_chatgpt_sign_in(monkeypatch, tmp_path, capsys, mode, warns):
    from cloakroom import cli
    home = tmp_path / "x"
    home.mkdir()
    fake = {"OPENAI_API_KEY": None, "tokens": {"access_token": "fake-access-value-1234"}}
    if mode:
        fake["auth_mode"] = mode
    (home / "auth.json").write_text(json.dumps(fake))
    monkeypatch.setenv("CODEX_HOME", str(home))
    for extra in (["--print"], []):
        assert cli.main(["proxy", "install", "codex", *extra]) == 0
        out = capsys.readouterr()
        assert ("signed in with ChatGPT" in out.err) is warns
        assert "fake-access-value-1234" not in out.out + out.err  # no value from the file is printed


def test_codex_install_without_an_auth_file_does_not_warn(monkeypatch, tmp_path, capsys):
    from cloakroom import cli
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "none"))
    assert cli.main(["proxy", "install", "codex", "--print"]) == 0
    assert "ChatGPT" not in capsys.readouterr().err
