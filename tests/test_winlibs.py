"""Windows DLL folders for torch / onnxruntime (msvc-runtime), and a clear message when a DLL is still missing."""
import os

import pytest

from cloakroom import engine, winlibs


def test_add_dll_dirs_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(winlibs, "_windows", lambda: False)
    before = list(winlibs._HANDLES)
    winlibs.add_dll_dirs()
    assert winlibs._HANDLES == before


def test_add_dll_dirs_registers_existing_folders_and_swallows_oserror(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(winlibs, "_windows", lambda: True)
    monkeypatch.setattr(os, "add_dll_directory", lambda d: calls.append(d) or object(), raising=False)
    monkeypatch.setattr(winlibs.sys, "prefix", str(tmp_path))
    (tmp_path / "Scripts").mkdir()
    monkeypatch.setattr(winlibs, "_DONE", set())
    winlibs.add_dll_dirs()
    assert str(tmp_path / "Scripts") in calls
    n = len(calls)
    winlibs.add_dll_dirs()  # idempotent
    assert len(calls) == n

    def boom(d):
        raise OSError("nope")
    monkeypatch.setattr(os, "add_dll_directory", boom, raising=False)
    monkeypatch.setattr(winlibs, "_DONE", set())
    winlibs.add_dll_dirs()  # must not raise


def test_vcpp_hint_names_the_runtime_on_windows(monkeypatch):
    monkeypatch.setattr(winlibs, "_windows", lambda: True)
    e = OSError(0, "The specified module could not be found.")
    e.winerror = 126
    msg = winlibs.vcpp_hint(e)
    assert "Visual C++" in msg and "MSVCP140.dll" in msg
    assert "OSError" in winlibs.vcpp_hint(RuntimeError("x")) or "RuntimeError" in winlibs.vcpp_hint(RuntimeError("x"))


def test_torch_oserror_becomes_model_unavailable_not_a_traceback(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "torch":
            raise OSError("dll")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(engine.ModelUnavailable):
        engine._torch_model()
