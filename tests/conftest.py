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
