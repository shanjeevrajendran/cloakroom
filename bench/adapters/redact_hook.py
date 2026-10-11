"""SilentAutomaton/redact-hook (pure regex PostToolUse), default config."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "competitors", "redact-hook"))
import redact_output as R  # noqa: E402

NAME = "redact-hook (default)"
_RULES, _ALLOW = R.active_rules({}), R.allow_patterns({})


def mask(text):
    return R.redact_regex(text, _RULES, _ALLOW), False
