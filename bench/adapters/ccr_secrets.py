import _ts
NAME = "claude-code-redact (secrets)"


def mask(text):
    return _ts.run(text)["ccrSecrets"], False
