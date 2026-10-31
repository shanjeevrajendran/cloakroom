"""1.0: outgoing-data control: the where-it-went ledger, the approved-sites list, the header guard. Synthetic data."""
import io
import json
import subprocess
import sys

import pytest

EMAIL = "henrik.dahl@quillfield.com"


def hook(event, data, monkeypatch, capsys):
    from cloakroom import hook as h
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    h.main(event)
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def pre(tool, args, monkeypatch, capsys, cwd="."):
    return hook("pre", {"session_id": "e1", "tool_name": tool, "cwd": str(cwd), "tool_input": args}, monkeypatch, capsys)


def bash(command, monkeypatch, capsys, cwd="."):
    return pre("Bash", {"command": command}, monkeypatch, capsys, cwd)


def decision(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def placeholder(value="ingrid.voss@quillfield.com", session="e1"):
    from cloakroom import client
    return client.mask(f"write to {value}", session)["text"].split()[-1]


def last_event():
    from cloakroom import events
    return next(e for e in events.read() if e["event"] == "pre")


def turn_on(hosts):
    from cloakroom import config
    config.set_allowed_hosts(hosts)
    path = config.HOME / "config.json"
    cfg = json.loads(path.read_text())
    cfg["egress"] = "allowlist"
    path.write_text(json.dumps(cfg))


# --- the ledger ---------------------------------------------------------------------------------------------

def test_command_is_logged_with_host_and_kinds_only(monkeypatch, capsys):
    ph = placeholder()
    bash(f"curl -s https://API.Example.com:8443/v1/send?to={ph}", monkeypatch, capsys)
    ev = last_event()
    assert ev["dest"] == ["api.example.com"] and ev["carries"] == {"EMAIL": 1}
    from cloakroom import config
    assert "ingrid.voss" not in "".join(p.read_text() for p in config.HOME.glob("events-*.jsonl"))


def test_webfetch_and_mcp_calls_are_logged(monkeypatch, capsys):
    ph = placeholder()
    pre("WebFetch", {"url": f"https://docs.example.com/page?q={ph}", "prompt": "summarise"}, monkeypatch, capsys)
    ev = last_event()
    assert ev["dest"] == ["docs.example.com"] and ev["carries"] == {"EMAIL": 1}
    pre("WebSearch", {"query": "how do placeholders work"}, monkeypatch, capsys)
    assert last_event()["dest"] == ["web-search"]
    pre("mcp__crm__lookup", {"customer": ph}, monkeypatch, capsys)  # a server the config doesn't name
    ev = last_event()
    assert ev["dest"] == ["mcp:crm"] and ev["carries"] == {"EMAIL": 1}


def test_mcp_server_from_config(tmp_path, monkeypatch, capsys):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "remote-crm": {"type": "http", "url": "https://mcp.crm.example.com/sse"}, "files": {"command": "npx"}}}))
    pre("mcp__remote-crm__lookup", {"id": 7}, monkeypatch, capsys, cwd=tmp_path)
    assert last_event()["dest"] == ["mcp.crm.example.com"]
    from cloakroom import events
    n = len(events.read())
    pre("mcp__files__read", {"path": "notes.txt"}, monkeypatch, capsys, cwd=tmp_path)  # started by a command: local
    assert "dest" not in events.read()[0] and len(events.read()) == n + 1


def test_local_and_non_sending_commands_are_not_logged(monkeypatch, capsys):
    for cmd in ("ls -la", "curl http://localhost:3000/health", "git remote -v", "echo https://example.com"):
        bash(cmd, monkeypatch, capsys)
        assert "dest" not in last_event(), cmd


def test_report_destinations_counts(monkeypatch, capsys):
    ph = placeholder()
    for _ in range(2):
        bash(f"curl https://a.example.com/x?e={ph}", monkeypatch, capsys)
    bash("curl https://b.example.com/", monkeypatch, capsys)
    turn_on(["a.example.com"])
    bash("curl https://b.example.com/", monkeypatch, capsys)  # held
    from cloakroom import cli, report
    rows = {r["host"]: r for r in report.destinations()}
    assert rows["a.example.com"]["calls"] == 2 and rows["a.example.com"]["kinds"] == {"EMAIL": 2}
    assert rows["b.example.com"]["calls"] == 2 and rows["b.example.com"]["held"] == 1
    assert cli.main(["report", "--destinations"]) == 0
    text = capsys.readouterr().out
    assert "host,calls,held,kinds" in text and "a.example.com,2,0,EMAILx2" in text and "b.example.com,2,1," in text
    assert json.loads((cli.main(["report", "--destinations", "--format", "json"]), capsys.readouterr().out)[1]
                      )["destinations"][0]["host"] == "a.example.com"


# --- the approved-sites list ----------------------------------------------------------------------------------

def test_list_off_is_todays_behaviour(monkeypatch, capsys):
    from cloakroom import config
    config.set_allowed_hosts(["only.example.com"])  # a list without the mode
    assert decision(bash("curl https://other.example.net/", monkeypatch, capsys)) is None
    assert decision(pre("WebFetch", {"url": "https://other.example.net/"}, monkeypatch, capsys)) is None


def test_list_on_holds_unlisted_and_passes_listed_and_wildcard(monkeypatch, capsys):
    turn_on(["api.example.com", "*.cdn.example.com"])
    out = bash("curl https://other.example.net/x", monkeypatch, capsys)
    assert decision(out) == "deny" and "other.example.net" in reason(out) and "approved sites" in reason(out)
    assert "allow-host" in reason(out) and last_event()["action"] == "held"
    assert decision(bash("curl https://api.example.com/x", monkeypatch, capsys)) is None
    assert decision(bash("curl https://img.cdn.example.com/x", monkeypatch, capsys)) is None
    assert decision(bash("curl https://cdn.example.com/x", monkeypatch, capsys)) == "deny"  # *. means subdomains only
    assert decision(pre("WebFetch", {"url": "https://other.example.net/"}, monkeypatch, capsys)) == "deny"
    assert decision(pre("WebFetch", {"url": "https://api.example.com/"}, monkeypatch, capsys)) is None
    assert decision(pre("WebSearch", {"query": "x"}, monkeypatch, capsys)) == "deny"
    assert decision(bash("curl http://localhost:3000/", monkeypatch, capsys)) is None  # never leaves this machine


def test_list_on_holds_what_it_cannot_place_and_cr_allow_passes_it_once(monkeypatch, capsys):
    turn_on(["api.example.com"])
    out = bash("aws s3 ls", monkeypatch, capsys)
    assert decision(out) == "deny" and "can't tell where" in reason(out)
    hook("prompt", {"session_id": "e1", "prompt": "cr: allow"}, monkeypatch, capsys)
    assert decision(bash("aws s3 ls", monkeypatch, capsys)) is None
    assert last_event()["action"] == "allowed once"
    assert decision(bash("aws s3 ls", monkeypatch, capsys)) == "deny"  # once


def test_git_push_goes_to_its_remote(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://git.example.com/o/r.git"], check=True)
    bash("git push origin main", monkeypatch, capsys, cwd=tmp_path)
    assert last_event()["dest"] == ["git.example.com"]


def test_hosts_in_commands_across_shells():
    from cloakroom import egress
    hosts = egress.command_hosts
    assert hosts("ssh -i key.pem bob@box.example.com ls") == ["box.example.com"]
    assert hosts("scp C:\\Users\\bob\\a.txt h2.example.org:/t") == ["h2.example.org"]  # the drive is not a host
    assert hosts("git clone git@github.com:o/r.git") == ["github.com"]
    assert hosts("curl -o out.txt example.com/a") == ["example.com"]
    assert hosts("Invoke-WebRequest -Uri https://x.example.com/a -Headers @{a='b'}", True) == ["x.example.com"]
    assert hosts("python3 -c \"import urllib.request as u;u.urlopen('http://a.b.io/x')\"") == ["a.b.io"]


def test_allow_host_command(capsys):
    from cloakroom import cli, config
    path = config.HOME / "config.json"
    config.private_dir()
    path.write_text(json.dumps({"port": 40000}))
    assert cli.main(["allow-host", "https://Example.COM:443/x"]) == 0
    assert cli.main(["allow-host", "*.cdn.example.com"]) == 0
    assert json.loads(path.read_text()) == {"port": 40000, "allowed_hosts": ["example.com", "*.cdn.example.com"]}
    capsys.readouterr()
    assert cli.main(["allow-host", "--list"]) == 0 and "example.com" in capsys.readouterr().out
    assert cli.main(["allow-host", "example.com", "--remove"]) == 0
    assert config.load()["allowed_hosts"] == ["*.cdn.example.com"]
    for bad in ("*", "*example.com", "a b", "exa*mple.com"):
        assert cli.main(["allow-host", bad]) == 2, bad


def test_bad_egress_setting_is_refused():
    from cloakroom import config
    (config.HOME).mkdir(parents=True, exist_ok=True)
    (config.HOME / "config.json").write_text(json.dumps({"egress": "maybe"}))
    with pytest.raises(ValueError):
        config.load()


# --- the header guard -----------------------------------------------------------------------------------------

def test_email_in_a_url_is_held(monkeypatch, capsys):
    out = bash(f"curl 'https://track.example.com/hit?user={EMAIL}'", monkeypatch, capsys)
    assert decision(out) == "deny" and "an email address" in reason(out) and EMAIL not in reason(out)
    ev = last_event()
    assert ev["action"] == "held" and ev["kinds"] == {"EMAIL": 1} and ev["hold"] == "header-guard"
    assert EMAIL not in json.dumps(ev)


def test_username_in_a_header_is_held_and_env_does_not_decide_it(monkeypatch, capsys):
    from cloakroom import egress
    for var in ("USER", "USERNAME", "LOGNAME", "LNAME"):
        monkeypatch.setenv(var, "decoy-user")
    assert egress.own_username() and egress.own_username() != "decoy-user"
    monkeypatch.setattr(egress, "own_username", lambda: "Bob99")
    out = bash("curl -H 'X-Owner: bob99' https://api.example.com/", monkeypatch, capsys)  # any case: Windows names are
    assert decision(out) == "deny" and "your username" in reason(out)
    out = pre("PowerShell", {"command": "Invoke-RestMethod -Uri https://api.example.com/ -Headers @{'X-Owner'='BOB99'}"},
              monkeypatch, capsys)
    assert decision(out) == "deny" and "your username" in reason(out)
    out = pre("WebFetch", {"url": "https://api.example.com/users/bob99"}, monkeypatch, capsys)
    assert decision(out) == "deny" and "your username" in reason(out)
    assert decision(bash("curl https://api.example.com/bob999", monkeypatch, capsys)) is None  # another word
    assert decision(bash("cat C:\\Users\\Bob99\\notes.txt", monkeypatch, capsys)) is None  # a path is not a call


def test_clean_call_passes_and_cr_allow_passes_a_held_one_once(monkeypatch, capsys):
    assert decision(bash("curl -H 'Accept: application/json' https://api.example.com/v1/items", monkeypatch, capsys)) is None
    cmd = f"curl 'https://track.example.com/hit?user={EMAIL}'"
    assert decision(bash(cmd, monkeypatch, capsys)) == "deny"
    hook("prompt", {"session_id": "e1", "prompt": "cr: allow"}, monkeypatch, capsys)
    assert decision(bash(cmd, monkeypatch, capsys)) is None
    assert decision(bash(cmd, monkeypatch, capsys)) == "deny"


def test_placeholder_in_a_url_is_not_a_person_kind(monkeypatch, capsys):
    ph = placeholder()
    assert decision(bash(f"curl https://api.example.com/?to={ph}", monkeypatch, capsys)) != "deny"


def test_one_hold_names_both_reasons(monkeypatch, capsys):
    turn_on(["api.example.com"])
    out = bash(f"curl 'https://track.example.net/hit?user={EMAIL}'", monkeypatch, capsys)
    assert "track.example.net" in reason(out) and "an email address" in reason(out)
    assert last_event()["hold"] == "approved-sites+header-guard"
