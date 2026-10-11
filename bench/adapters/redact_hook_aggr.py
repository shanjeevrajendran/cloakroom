"""SilentAutomaton/redact-hook with its aggressive (entropy) mode on."""
import redact_hook

NAME = "redact-hook (aggressive)"
_RULES = redact_hook.R.active_rules({}, aggressive=True)


def mask(text):
    return redact_hook.R.redact_regex(text, _RULES, redact_hook._ALLOW), False
