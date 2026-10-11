import _ts
NAME = "claude-code-redact (+pii)"


def mask(text):
    return _ts.run(text)["ccrPii"], False
