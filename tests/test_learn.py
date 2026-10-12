"""Learn quietly: locked until 10 decisions; rules apply on their own only after the replay test; re-checked on labels."""
import pytest


def _vals(table):
    return lambda event_id: [{"value": v} for v in table.get(event_id, [])]


def _label(event_id, lab, items=()):
    from cloakroom import labels
    labels.put(event_id, lab, list(items))


def test_locked_until_ten_decisions():
    from cloakroom import learn
    with pytest.raises(ValueError):
        learn.set_mode("quiet", 9)
    assert learn.set_mode("quiet", 10) == "quiet" and learn.mode() == "quiet"


def test_replay_blocks_unmasking_what_you_kept():
    from cloakroom import learn
    vals = _vals({"c1": ["Grace Hopper"]})
    _label("c1", "correct")
    assert not learn.replay({"type": "allow", "text": "Grace Hopper", "kind": "PERSON"}, vals)[0]
    assert not learn.replay({"type": "allow", "text": "re:.*", "kind": "PERSON"}, vals)[0]  # too broad: safety set
    assert learn.replay({"type": "allow", "text": "Ada Lovelace", "kind": "PERSON"}, vals)[0]
    assert not learn.replay({"type": "allow", "text": "sk_x", "kind": "SECRET"}, vals)[0]  # secrets wait for you
    _label("f1", "false_alarm", [{"kind": "ACCOUNT", "value": "1048576"}])
    assert not learn.replay({"type": "always_mask", "text": "re:[0-9]{7}", "kind": "ACCOUNT"}, vals)[0]
    assert not learn.replay({"type": "always_mask", "text": "re:[a-z]+", "kind": "OTHER"}, vals)[0]  # ordinary text
    assert learn.replay({"type": "always_mask", "text": r"re:ACCT\-[0-9]{6}", "kind": "ACCOUNT"}, vals)[0]


def test_quiet_applies_then_pauses_on_conflict():
    from cloakroom import learn, userrules
    vals = _vals({"c2": ["Ada Lovelace"]})
    learn.set_mode("quiet", 10)
    _label("f1", "false_alarm", [{"kind": "PERSON", "value": "Ada Lovelace"}])
    added = userrules.suggest("f1", "false_alarm", [{"kind": "PERSON", "value": "Ada Lovelace"}])
    learn.after_label(added, vals)
    [r] = userrules.load()
    assert r["state"] == "active" and r["auto"] and "replay" in r["replay"]
    _label("c2", "correct")  # now you say Ada Lovelace should be masked
    changed = learn.after_label([], vals)
    assert changed and userrules.load()[0]["state"] == "suggested" and "paused" in userrules.load()[0]["why"]


def test_suggest_mode_only_reports():
    from cloakroom import learn, userrules
    added = userrules.suggest("f1", "false_alarm", [{"kind": "PERSON", "value": "Ada Lovelace"}])
    learn.after_label(added, _vals({}))
    [r] = userrules.load()
    assert r["state"] == "suggested" and r["replay"].startswith("replay:")
