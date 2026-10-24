import base64
import importlib

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own CLOAKROOM_HOME, rules mode, and an environment vault key (never the OS store)."""
    monkeypatch.setenv("CLOAKROOM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLOAKROOM_MODE", "rules")
    monkeypatch.setenv("CLOAKROOM_VAULT_KEY", base64.b64encode(b"k" * 32).decode())
    from cloakroom import config, crypto
    importlib.reload(config)
    crypto._KEY = None
    yield


def tag(text, session="default"):
    """A test's PII_KIND_n as the session issued it (PII_KIND_n_<tag>): only issued placeholders are restored."""
    import re
    from cloakroom import config
    from cloakroom.vault import Vault
    names = {p.rsplit("_", 1)[0]: p for p in Vault(config.vault_path(session)).by_placeholder}
    return re.sub(r"\bPII_[A-Z]+_\d+\b", lambda m: names.get(m.group(), m.group()), text)


def untag(text):
    """Output with the tags taken off, to compare with PII_KIND_n."""
    import re
    return re.sub(r"\b(PII_[A-Z]+_\d+)_[a-z2-7]{6}\b", r"\1", text)
