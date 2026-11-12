"""A hook answers before its agent stops waiting for it, and the service doesn't do the same work twice.

The agents give a hook 30 to 120 seconds. Past that the agent decides what happens, and Gemini goes on with the
unchecked original. Before 1.5 each call to the service had 30 seconds of its own, a call that timed out was sent
again (so the service did the work twice, one after the other) and a second service was started for nothing."""
import json
import socket
import sys
import threading
import time
from collections import namedtuple
from http.server import ThreadingHTTPServer

import pytest

from cloakroom import client, daemon, engine, hook

Span = namedtuple("Span", "start end label score")
NAME = "Ingrid Thies"


class SlowModel:
    """Finds NAME; every window it reads takes `seconds`."""

    def __init__(self, seconds=0.0):
        self.seconds, self.calls = seconds, 0

    def predict(self, text):
        self.calls += 1
        time.sleep(self.seconds)
        i = text.find(NAME)
        return ([Span(i, i + len(NAME), "private_person", 0.99)] if i >= 0 else []), None


def _long_text(windows=6):
    """About `windows` model windows of prose, every line different, with NAME in the last line."""
    lines, size, i = [], 0, 0
    while size < windows * engine.CHUNK_CHARS - 200:
        lines.append(f"Line {i}: the service restarted after the deploy and the queue drained without any errors.")
        size += len(lines[-1]) + 1
        i += 1
    return "\n".join(lines) + f"\nSigned by {NAME}.\n"


@pytest.fixture
def service(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), daemon.Handler)
    srv.mode = "full"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", str(srv.server_address[1]))
    monkeypatch.setattr(client, "start_background", lambda *a: pytest.fail("a second service was started"))
    yield srv
    srv.shutdown()
    daemon.VAULTS.clear()


# --- the model's answer for a window is kept

def test_the_same_text_is_read_by_the_model_once(monkeypatch):
    model = SlowModel()
    monkeypatch.setattr(engine, "_MODEL", model)
    text = _long_text(3)
    first = engine.model_findings(text)
    calls = model.calls
    assert calls >= 3 and engine.model_findings(text) == first and model.calls == calls


def test_a_file_checked_before_a_command_is_not_read_again_as_its_output(monkeypatch):
    model = SlowModel()
    monkeypatch.setattr(engine, "_MODEL", model)
    text = _long_text(3)
    engine.model_findings(text)
    calls = model.calls
    engine.model_findings(text.rstrip("\n"))  # the output, as the agent hands it over: only the last window is new
    assert model.calls == calls + 1


def test_another_model_starts_with_nothing_kept(monkeypatch):
    a, b = SlowModel(), SlowModel()
    monkeypatch.setattr(engine, "_MODEL", a)
    engine.model_findings("Signed by Ingrid Thies.")
    monkeypatch.setattr(engine, "_MODEL", b)
    engine.model_findings("Signed by Ingrid Thies.")
    assert a.calls == b.calls == 1


def test_what_is_kept_holds_no_text(monkeypatch):
    monkeypatch.setattr(engine, "_MODEL", SlowModel())
    engine.model_findings("Signed by Ingrid Thies.")
    kept = repr(list(engine._READ.items()))
    assert "Ingrid" not in kept and "Signed" not in kept
    assert all(isinstance(k, bytes) and len(k) == 32 for k in engine._READ)


def test_only_the_most_recent_windows_are_kept(monkeypatch):
    monkeypatch.setattr(engine, "_MODEL", SlowModel())
    monkeypatch.setattr(engine, "KEEP_WINDOWS", 5)
    for i in range(20):
        engine.model_findings(f"line {i} signed by Ingrid Thies.")
    assert len(engine._READ) == 5


# --- a check stops when its time is up

def test_a_model_check_stops_between_windows_when_its_time_is_up(monkeypatch):
    model = SlowModel(0.05)
    monkeypatch.setattr(engine, "_MODEL", model)
    with engine.time_limit(0.12), pytest.raises(engine.OutOfTime):
        engine.mask(_long_text(8), mode="full")
    assert 2 <= model.calls <= 4  # stopped: not all 8 windows
    assert isinstance(engine.OutOfTime("x"), engine.ModelUnavailable)  # callers fail closed on it as they did


def test_without_a_limit_and_after_one_the_whole_text_is_read(monkeypatch):
    monkeypatch.setattr(engine, "_MODEL", SlowModel())
    with engine.time_limit(None):
        assert NAME not in engine.mask(_long_text(3), mode="full").text
    with engine.time_limit(0.01):
        pass
    time.sleep(0.02)
    assert NAME not in engine.mask(_long_text(4), mode="full").text  # the limit ended with its block


# --- the hook's clock

def test_a_hook_s_calls_share_its_agent_s_time_limit():
    assert client.seconds_left() == client.REQUEST_SECONDS  # no hook running: one request's own time
    client.start_clock("pre")
    try:
        assert 40 < client.seconds_left() < 60  # the agent waits 60 s for this hook
        assert client.seconds_left(5) == 5
    finally:
        client.stop_clock()
    client.start_clock("post")
    try:
        assert 90 < client.seconds_left() < 120
    finally:
        client.stop_clock()


def test_the_clock_stops_when_the_hook_has_answered():
    hook.handle("pre", {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
    assert client.seconds_left() == client.REQUEST_SECONDS


def test_with_no_time_left_the_call_fails_closed_without_asking(service, monkeypatch):
    monkeypatch.setattr(client, "_UNTIL", [time.monotonic() - 1])
    with pytest.raises(client.Unavailable, match="longer than the agent waits"):
        client.mask("Signed by Ingrid Thies.", "s")


def test_a_call_that_times_out_is_not_sent_again(monkeypatch):
    quiet = socket.socket()  # something is listening and never answers: a service busy with a long check
    quiet.bind(("127.0.0.1", 0))
    quiet.listen(1)
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", str(quiet.getsockname()[1]))
    monkeypatch.setattr(client, "start_background", lambda *a: pytest.fail("a second service was started"))
    monkeypatch.setattr(client, "is_cloakroom", lambda port: True)
    t0 = time.monotonic()
    try:
        with pytest.raises(client.Unavailable, match="longer than the agent waits"):
            client._post("/mask", {"text": "x", "session": "s"}, timeout=0.3)
    finally:
        quiet.close()
    assert time.monotonic() - t0 < 2  # once, not twice with a wait between


# --- through the service

def test_the_service_checks_in_full_when_there_is_time(service, monkeypatch):
    monkeypatch.setattr(engine, "_MODEL", SlowModel())
    r = client.mask(_long_text(2), "s")
    assert NAME not in r["text"] and r["mode"] == "full"


def test_the_service_stops_a_check_nobody_waits_for_and_the_hook_withholds(service, monkeypatch, capsys):
    model = SlowModel(0.2)
    monkeypatch.setattr(engine, "_MODEL", model)
    monkeypatch.setattr(client, "HOOK_SECONDS", {**client.HOOK_SECONDS, "post": 3.2})  # 0.72 s after the margin
    text = _long_text(10)
    out = hook.handle("post", {"session_id": "s", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "x"},
                               "tool_response": {"stdout": text, "stderr": ""}})
    shown = json.dumps(out)
    assert NAME not in shown and "couldn't check this" in shown and "longer than the agent waits" in shown
    time.sleep(0.5)
    assert model.calls <= 5  # the service stopped too: it did not read all 10 windows for nobody


def test_a_call_that_waited_past_its_time_for_another_check_is_not_run(service, monkeypatch):
    model = SlowModel(0.15)
    monkeypatch.setattr(engine, "_MODEL", model)
    first = threading.Thread(target=lambda: client.mask(_long_text(6), "a"))  # about 0.9 s under the service's lock
    first.start()
    time.sleep(0.1)
    with pytest.raises(client.Unavailable):
        client._post("/mask", {"text": "Signed by Ingrid Thies, second caller.", "session": "b"}, timeout=0.3)
    first.join()
    calls = model.calls
    time.sleep(0.3)
    assert model.calls == calls  # the second call's text was never read: its caller had gone


# --- found by the review of this branch

def test_no_time_left_means_out_of_time_not_no_limit(monkeypatch):
    model = SlowModel()
    monkeypatch.setattr(engine, "_MODEL", model)
    for seconds in (0, 0.0, -3):
        with engine.time_limit(seconds), pytest.raises(engine.OutOfTime):
            engine.mask(_long_text(2), mode="full")
    assert model.calls == 0


def test_the_service_does_not_start_a_check_for_a_caller_with_no_time_left(service, monkeypatch):
    model = SlowModel()
    monkeypatch.setattr(engine, "_MODEL", model)
    monkeypatch.setattr(client, "seconds_left", lambda most=None: 5.0)  # the request itself gets through
    real = client.urllib.request.Request

    def no_time(url, data, headers):  # as sent with nothing left on the hook's clock
        return real(url, json.dumps({**json.loads(data), "seconds": 0.0}).encode(), headers)
    monkeypatch.setattr(client.urllib.request, "Request", no_time)
    with pytest.raises(client.Unavailable, match="longer than the agent waits"):
        client._post("/mask", {"text": _long_text(2), "session": "s"})
    assert model.calls == 0


def test_without_a_hook_clock_the_first_start_wait_is_what_it_was(monkeypatch):
    waited = []
    quiet = socket.socket()
    quiet.bind(("127.0.0.1", 0))
    port = quiet.getsockname()[1]
    quiet.close()  # nothing listens there
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", str(port))
    monkeypatch.setattr(client, "start_background", lambda *a: None)
    monkeypatch.setattr(client, "_wait_until_up", lambda p, seconds: waited.append(seconds) or False)
    with pytest.raises(client.Unavailable):
        client._post("/mask", {"text": "x", "session": "s"})  # the proxy, the command line: no clock
    client.start_clock("start")
    try:
        with pytest.raises(client.Unavailable):
            client._post("/mask", {"text": "x", "session": "s"})
    finally:
        client.stop_clock()
    assert waited[0] == client.STARTUP_WAIT and waited[1] < 30


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps its 2 second try, then runs without the lock")
def test_a_session_file_held_by_a_stuck_process_fails_the_mask_instead_of_waiting_for_ever(monkeypatch):
    from cloakroom import config, store, vault
    monkeypatch.setattr(vault, "LOCK_SECONDS", 0.2)
    v = vault.Vault(config.vault_path("stuck"))
    v.placeholder("first@quillmere.net", "EMAIL")
    held, done = threading.Event(), threading.Event()

    def holder():  # another hook, stuck with the session's lock
        with store.locked(config.vault_path("stuck").with_suffix(".lock")):
            held.set()
            done.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    t0 = time.monotonic()
    try:
        with pytest.raises(store.Busy):
            engine.mask("write to second@quillmere.net", v, "rules")
        assert time.monotonic() - t0 < 2
    finally:
        done.set()
        t.join()
    assert "second@quillmere.net" not in engine.mask("write to second@quillmere.net", v, "rules").text  # free again
