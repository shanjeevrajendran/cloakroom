"""0.6.5 finance: US bank routing and account numbers (kind BANK) and the money-movement hold. All values are made up:
routing numbers are built to pass the ABA checksum, account numbers are random digits."""
import io
import json
import sys

import pytest

from cloakroom import engine, rules

KEY = "sk_live_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1"  # a Stripe-key shape, not a real one


def _aba(prefix8: str) -> str:
    return next(prefix8 + str(c) for c in range(10) if rules._aba_ok(prefix8 + str(c)))


R, R2 = _aba("07391482"), _aba("26408317")
ACCT = "48210937715"


def banks(text, mode="rules"):
    found, _ = engine.detect(text, mode)
    return [text[f.start:f.end] for f in found if f.kind == "BANK"]


def test_aba_checksum():
    assert rules._aba_ok(R) and rules._aba_ok(R2)
    assert not rules._aba_ok(R[:8] + str((int(R[8]) + 1) % 10))  # one digit off
    assert not rules._aba_ok("000000000") and not rules._aba_ok("990000009")  # one digit / no Fed prefix


@pytest.mark.parametrize("text,values", [
    (f'{{"routing_number": "{R}", "account_number": "{ACCT}"}}', [R, ACCT]),
    (f"routing: {R}\naccount: {ACCT}", [R, ACCT]),
    (f"ROUTING_NUMBER={R}\nACCOUNT_NUMBER=000{ACCT}", [R, "000" + ACCT]),
    (f"name,routing_number,account_number\nTeodor Vashti,{R},{ACCT}", [R, ACCT]),  # CSV row, column names only
    (f"routing_number: {R}\naccount_number: {ACCT}\n", [R, ACCT]),  # YAML
    (f"Please wire to ABA {R}, account no. 7720-4419-03.", [R, "7720-4419-03"]),
    (f'{{"aba": "{R2}", "account_id": "9930021774"}}', [R2, "9930021774"]),  # plain account key beside a routing number
    (f'routingNumber: "{R}", accountNumber: "{ACCT}"', [R, ACCT]),
    ("Beneficiary account: 30019928475", ["30019928475"]),
    ("DDA #5512093388", ["5512093388"]),
    ("Account number: 4417 2290 1185 3306", ["4417 2290 1185 3306"]),  # spaced: whole number, not the first group
    (f"622{R}{ACCT:<17}0000012500{'ID0042':<15}{'TEODOR VASHTI':<22}  0{R[:8]}0000001", [R, ACCT]),  # NACHA entry
])
def test_bank_numbers_masked(text, values):
    assert sorted(banks(text)) == sorted(values)


@pytest.mark.parametrize("text", [
    "Order #482109377 shipped", "Invoice INV-2024-48210937", "Call (415) 762-3381", "ZIP 94110-2231",
    "aws account id 123456789012", "account_id: 123456789012", "savings: 1200.00", "checking 1234 files",
    "routing: 123456789", f"id {R} tracking", "account 2 of 5", "transit time 300 ms",
    "Tracking number 1Z999AA10123456784", "bank holiday 2024", "savings account balance $4521.33",
    f'{{"account": "{ACCT}"}}', "account number: 0000000000", f"routing: {R}0",
    "ticket for ACCT-482913 and ACCT-100200", f"RTN_{R}",  # id prefixes, not labels
])
def test_finance_look_alikes_not_bank(text):
    assert banks(text) == []


@pytest.mark.parametrize("text", [
    "dcf54dda262dda2019051dae0562c015d049a05d",  # a git SHA: "dda" then digits inside hex
    "commit 7e1f0c2dda48210937715ab09c3e5d1f2a4b6c8d", f'"encryptedKey": "02bb8d5c39dda{ACCT}af96c82fad"',
    f"key 3aba{R}f0e1", f"0x7fbank{R}c3", f"acct{ACCT}", f"RTN{R}",  # a label glued to letters or digits
])
def test_label_words_inside_hex_or_ids_not_bank(text):
    assert banks(text) == []


@pytest.mark.parametrize("text,value", [("DDA 482109377", "482109377"), (f"dda: {ACCT}", ACCT),
                                        (f"Routing ABA {R}", R), (f"(acct# {ACCT})", ACCT)])
def test_standalone_labels_still_bank(text, value):
    assert banks(text) == [value]


@pytest.mark.parametrize("text", ["Account Number: 123456789", "Bank Account Number: 1234567890",
                                  "account no: 12345678", "Account Number: 111111111"])
def test_counting_placeholders_not_bank(text):
    assert banks(text) == []


@pytest.mark.parametrize("text,value", [("Account Number: 55783492", "55783492"), (f"Account Number: {ACCT}", ACCT),
                                        ("Account Number: 123456780", "123456780")])  # one digit off the count
def test_realistic_account_numbers_still_bank(text, value):
    assert banks(text) == [value]


def test_paired_needs_same_record():
    assert banks(f"routing: {R}\n\naccount: {ACCT}") == [R]  # a blank line ends the record


# ---- money-movement hold ----

def run_hook(event, data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main(event)
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def call(tool, tool_input, monkeypatch, capsys):
    return run_hook("pre", {"session_id": "m", "tool_name": tool, "cwd": "/tmp", "tool_input": tool_input},
                    monkeypatch, capsys)


def held(out):
    h = (out or {}).get("hookSpecificOutput") or {}
    return h.get("permissionDecision") == "deny", h.get("permissionDecisionReason", "")


def allow(monkeypatch, capsys):
    run_hook("prompt", {"session_id": "m", "prompt": "cr: allow"}, monkeypatch, capsys)


TRANSFER = "curl -X POST https://api.stripe.com/v1/transfers -d amount=1000 -d currency=usd -d destination=acct_x"


def test_stripe_transfer_held_balance_passes(monkeypatch, capsys):
    hit, why = held(call("Bash", {"command": TRANSFER}, monkeypatch, capsys))
    assert hit and "move money (api.stripe.com/v1/transfers)" in why and "cr: allow" in why
    assert call("Bash", {"command": "curl https://api.stripe.com/v1/balance"}, monkeypatch, capsys) is None
    assert call("Bash", {"command": "curl https://api.stripe.com/v1/payouts?limit=3"}, monkeypatch, capsys) is None


def test_allow_once(monkeypatch, capsys):
    assert held(call("Bash", {"command": TRANSFER}, monkeypatch, capsys))[0]
    allow(monkeypatch, capsys)
    assert call("Bash", {"command": TRANSFER + " -d x=1"}, monkeypatch, capsys) is not None  # changed: held again
    assert held(call("Bash", {"command": TRANSFER + " -d x=1"}, monkeypatch, capsys))[0]
    allow(monkeypatch, capsys)
    assert call("Bash", {"command": TRANSFER + " -d x=1"}, monkeypatch, capsys) is None
    assert held(call("Bash", {"command": TRANSFER + " -d x=1"}, monkeypatch, capsys))[0]  # once only


@pytest.mark.parametrize("tool,tool_input", [
    ("PowerShell", {"command": "Invoke-RestMethod -Method Post -Uri https://api-m.paypal.com/v1/payments/payouts "
                               "-Body $payload"}),
    ("Bash", {"command": "stripe payouts create --amount 1000 --currency usd"}),
    ("Bash", {"command": "curl -s https://sandbox.plaid.com/transfer/create --json @t.json"}),
    ("mcp__bank__send_money", {"amount": 25, "to": "PII_PERSON_1"}),
    ("mcp__stripe__create_payout", {"amount": 1000}),
    ("mcp__acme__initiateTransfer", {"amount": 5}),
    ("mcp__http__request", {"method": "POST", "url": "https://api.increase.com/ach_transfers", "body": "{}"}),
])
def test_hold_paths(tool, tool_input, monkeypatch, capsys):
    hit, why = held(call(tool, tool_input, monkeypatch, capsys))
    assert hit and "move money" in why


@pytest.mark.parametrize("tool,tool_input", [
    ("mcp__stripe__list_payouts", {}), ("mcp__bank__get_payment", {"id": "p1"}),
    ("mcp__http__request", {"method": "GET", "url": "https://api.increase.com/ach_transfers"}),
    ("WebFetch", {"url": "https://api.stripe.com/v1/transfers", "prompt": "docs"}),
    ("Bash", {"command": "rsync -a src/ host:/srv/transfers/"}),
])
def test_reads_and_look_alikes_pass(tool, tool_input, monkeypatch, capsys):
    assert not held(call(tool, tool_input, monkeypatch, capsys))[0]


def test_mcp_allow_once(monkeypatch, capsys):
    args = {"amount": 1000}
    assert held(call("mcp__stripe__create_payout", args, monkeypatch, capsys))[0]
    allow(monkeypatch, capsys)
    assert call("mcp__stripe__create_payout", args, monkeypatch, capsys) is None
    assert held(call("mcp__stripe__create_payout", args, monkeypatch, capsys))[0]


def _config(**kw):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps(kw))


def test_switch_off(monkeypatch, capsys):
    _config(money_hold=False)
    assert call("Bash", {"command": TRANSFER}, monkeypatch, capsys) is None
    assert call("mcp__stripe__create_payout", {"amount": 1}, monkeypatch, capsys) is None


def test_user_lists_extend(monkeypatch, capsys):
    _config(money_hosts=["payments.internal.example/api/wires"], money_tools=["disburse"])
    assert held(call("Bash", {"command": "curl -d @w.json https://payments.internal.example/api/wires/new"},
                     monkeypatch, capsys))[0]
    assert call("Bash", {"command": "curl -d @w.json https://payments.internal.example/api/status"},
                monkeypatch, capsys) is None
    assert held(call("mcp__erp__disburse_funds", {}, monkeypatch, capsys))[0]


def test_egress_and_money_one_hold(monkeypatch, capsys):
    cmd = f"curl -X POST https://api.stripe.com/v1/payouts -u {KEY}: -d amount=1000"
    hit, why = held(call("Bash", {"command": cmd}, monkeypatch, capsys))
    assert hit and "off this machine" in why and "move money (api.stripe.com/v1/payouts)" in why
    assert KEY not in why
    allow(monkeypatch, capsys)
    assert call("Bash", {"command": cmd}, monkeypatch, capsys) is None  # one cr: allow covers both


def test_hold_logged_without_values(monkeypatch, capsys):
    from cloakroom import config, events
    call("Bash", {"command": TRANSFER}, monkeypatch, capsys)
    log = (config.HOME / "hook.log").read_text()
    assert "held money: api.stripe.com/v1/transfers" in log and "acct_x" not in log
    ev = events.read()[-1]
    assert ev["action"] == "held" and ev["money"] == "api.stripe.com/v1/transfers"
