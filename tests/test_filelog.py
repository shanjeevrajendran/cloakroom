"""File log: real values Cloakroom restored into a file are masked again on later reads, in this session and in
new ones, while the file still holds them. Synthetic data; rules mode, where detection alone misses these names."""
import io
import json
import sys

import pytest

NAME = "Teodor Vashti"  # rules mode doesn't find a bare name: only the file log can
EMAIL = "t.vashti@quillmere.net"


def run_hook(event, data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main(event)
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _vault(session):
    from cloakroom import config
    from cloakroom.vault import Vault
    return Vault(config.vault_path(session))


def _write_via_hooks(path, content, monkeypatch, capsys, session="a", tool_use_id="tu1"):
    """Claude writes content holding placeholders: pre restores, the tool writes, post logs."""
    data = {"session_id": session, "tool_name": "Write", "cwd": str(path.parent), "tool_use_id": tool_use_id,
            "tool_input": {"file_path": str(path), "content": content}}
    pre = run_hook("pre", data, monkeypatch, capsys)
    written = pre["hookSpecificOutput"]["updatedInput"]["content"]
    path.write_text(written)
    run_hook("post", {**data, "tool_input": pre["hookSpecificOutput"]["updatedInput"], "tool_response": {}},
             monkeypatch, capsys)
    return written


def _read(path, monkeypatch, capsys, session, text=None):
    out = run_hook("post", {"session_id": session, "tool_name": "Read", "cwd": str(path.parent),
                            "tool_input": {"file_path": str(path)},
                            "tool_response": {"file": {"content": text if text is not None else path.read_text()}}},
                   monkeypatch, capsys)
    return out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"] if out else (text or path.read_text())


@pytest.fixture
def written(tmp_path, monkeypatch, capsys):
    ph = _vault("a").placeholder(NAME, "PERSON")
    path = tmp_path / "proj" / "owners.md"
    path.parent.mkdir()
    assert _write_via_hooks(path, f"{ph}\n", monkeypatch, capsys) == f"{NAME}\n"
    return path, ph


def test_without_the_log_rules_mode_misses_the_name(tmp_path, monkeypatch, capsys):
    p = tmp_path / "x.md"
    p.write_text(f"{NAME}\n")
    assert NAME in _read(p, monkeypatch, capsys, "z")


def test_same_session_re_read_gets_the_same_placeholder(written, monkeypatch, capsys):
    path, ph = written
    assert _read(path, monkeypatch, capsys, "a") == f"{ph}\n"


def test_new_session_re_read_is_masked_with_its_own_placeholder(written, monkeypatch, capsys):
    path, ph = written
    out = _read(path, monkeypatch, capsys, "b")
    assert NAME not in out and ph not in out
    assert _vault("b").by_value[NAME] in out  # restorable in session b, by b's own tag


def test_other_outputs_echoing_the_value_are_masked(written, monkeypatch, capsys):
    path, _ = written
    for tool, response in (("Bash", {"stdout": f"{path}:1:{NAME}"}),  # grep, cat, git diff, a test echo
                           ("Bash", {"stdout": f"AssertionError: expected '{NAME}'"})):
        out = run_hook("post", {"session_id": "c", "tool_name": tool, "cwd": str(path.parent),
                                "tool_input": {"command": "grep -rn owner ."}, "tool_response": response},
                       monkeypatch, capsys)
        assert NAME not in json.dumps(out)


def test_only_files_under_the_working_folder_apply(written, tmp_path, monkeypatch, capsys):
    other = tmp_path / "elsewhere"
    other.mkdir()
    out = run_hook("post", {"session_id": "c", "tool_name": "Bash", "cwd": str(other),
                            "tool_input": {"command": "cat x"}, "tool_response": {"stdout": f"{NAME}"}},
                   monkeypatch, capsys)
    assert out is None  # the log isn't searched outside the working folder
    path, _ = written
    out = run_hook("post", {"session_id": "c", "tool_name": "Read", "cwd": str(other),
                            "tool_input": {"file_path": str(path)},
                            "tool_response": {"file": {"content": f"{NAME}\n"}}}, monkeypatch, capsys)
    assert NAME not in json.dumps(out)  # a Read of the logged file itself applies from any working folder


def test_changed_file_drops_the_entry(written, monkeypatch, capsys):
    from cloakroom import filelog
    path, _ = written
    path.write_text("owner: someone else\n" + NAME[:3] + "\n")
    assert NAME in _read(path, monkeypatch, capsys, "b", text=f"{NAME}\n")
    assert filelog._load() == {}


def test_appended_file_keeps_the_span(written, monkeypatch, capsys):
    path, _ = written
    path.write_text(path.read_text() + "more notes\n")
    assert NAME not in _read(path, monkeypatch, capsys, "b")


def test_deleted_file_drops_the_entry(written, monkeypatch, capsys):
    from cloakroom import filelog
    path, _ = written
    path.unlink()
    assert filelog.known([str(path.parent)]) == {} and filelog._load() == {}


def test_log_holds_no_values(written):
    from cloakroom import config
    raw = (config.HOME / "filelog.bin").read_bytes()
    assert raw.startswith(b"CRV1") and NAME.encode() not in raw
    from cloakroom import filelog
    [entry] = filelog._load().values()
    assert NAME not in json.dumps(entry) and entry["spans"][0][3] == "PERSON"
    assert not list((config.HOME / "pending").iterdir())  # the pending note went once the write was logged


def test_edit_and_shell_writes_are_logged(tmp_path, monkeypatch, capsys):
    from cloakroom import filelog
    ph = _vault("a").placeholder(EMAIL, "EMAIL")
    p = tmp_path / "c.txt"
    p.write_text("contact: x\n")
    data = {"session_id": "a", "tool_name": "Edit", "cwd": str(tmp_path), "tool_use_id": "e1",
            "tool_input": {"file_path": str(p), "old_string": "x", "new_string": ph}}
    run_hook("pre", data, monkeypatch, capsys)
    p.write_text(f"contact: {EMAIL}\n")
    run_hook("post", {**data, "tool_response": {}}, monkeypatch, capsys)
    data = {"session_id": "a", "tool_name": "Bash", "cwd": str(tmp_path), "tool_use_id": "b1",
            "tool_input": {"command": f"echo {ph} > out.txt"}}
    run_hook("pre", data, monkeypatch, capsys)
    (tmp_path / "out.txt").write_text(EMAIL + "\n")
    run_hook("post", {**data, "tool_response": {"stdout": ""}}, monkeypatch, capsys)
    assert {filelog.norm(str(p)), filelog.norm(str(tmp_path / "out.txt"))} <= set(filelog._load())


def test_cap_drops_oldest_files_first(tmp_path, monkeypatch):
    from cloakroom import filelog
    monkeypatch.setattr(filelog, "MAX_SPANS", 2)
    for i in range(3):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"{NAME}\n")
        filelog.record(str(p), [(NAME, "PERSON", "PII_PERSON_1_aaaaaa")])
    assert sorted(filelog._load()) == [filelog.norm(str(tmp_path / f"f{i}.txt")) for i in (1, 2)]


@pytest.mark.parametrize("given", [r"C:\Users\Dev\Proj\notes.md", "c:/users/dev/proj/notes.md",
                                   r"\\?\C:\Users\Dev\Proj\notes.md", r"C:\Users\Dev\Proj\sub\..\notes.md"])
def test_windows_path_forms_fold_to_one(given):
    from cloakroom import filelog
    assert filelog.norm(given, windows=True) == "c:/users/dev/proj/notes.md"
    assert filelog.norm("notes.md", r"C:\Users\Dev\Proj", windows=True) == "c:/users/dev/proj/notes.md"
    assert filelog._under(filelog.norm(given, windows=True), filelog.norm(r"C:\Users\Dev", windows=True))


def test_unc_path_form():
    from cloakroom import filelog
    assert filelog.norm(r"\\?\UNC\server\share\a.md", windows=True) == "//server/share/a.md"


def test_full_mode_through_the_service(tmp_path, monkeypatch, capsys):
    """The hooks' real client -> service path in full mode (no OCR, no model weights): the roots reach the service
    and the file log masks a name the (fake, empty) model misses, in a new session."""
    import threading
    from http.server import ThreadingHTTPServer
    from cloakroom import daemon, engine
    monkeypatch.setattr(engine, "model_findings", lambda text: [])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), daemon.Handler)
    srv.mode = "full"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CLOAKROOM_MODE", "full")
        monkeypatch.setenv("CLOAKROOM_PORT", str(srv.server_address[1]))
        ph = _vault("a").placeholder(NAME, "PERSON")
        path = tmp_path / "owners.md"
        assert _write_via_hooks(path, f"{ph}\n", monkeypatch, capsys) == f"{NAME}\n"
        out = _read(path, monkeypatch, capsys, "b")
        assert NAME not in out and "PII_PERSON_1_" in out
    finally:
        srv.shutdown()
