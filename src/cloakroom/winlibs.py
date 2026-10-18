"""Windows only: make the Visual C++ runtime DLLs that the `msvc-runtime` package installs findable.

The plugin's installer adds msvc-runtime so a machine without the Visual C++ Redistributable can still load torch
and onnxruntime without admin rights. Those DLLs land in the environment's Scripts folder, which Windows does not
search when python.exe is the uv-managed interpreter in another folder: register the folders before any import of
torch or onnxruntime. A no-op everywhere else.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def _windows() -> bool:
    return sys.platform == "win32"


_HANDLES: list = []  # the handles must stay alive, or the folders are unregistered again
_DONE: set = set()  # folders already registered


def add_dll_dirs() -> None:
    if not _windows() or not hasattr(os, "add_dll_directory"):
        return
    for d in (Path(sys.prefix) / "Scripts", Path(sys.executable).parent):
        try:
            if d.is_dir() and d not in _DONE:
                _HANDLES.append(os.add_dll_directory(str(d)))
                _DONE.add(d)
        except OSError:
            pass



def vcpp_hint(e: BaseException) -> str:
    """A one-line explanation when an import failed because a DLL (WinError 126 / 127) could not be loaded."""
    if _windows() and isinstance(e, OSError) and getattr(e, "winerror", None) in (126, 127):
        return ("can't load (missing Microsoft Visual C++ runtime: MSVCP140.dll / VCRUNTIME140.dll). Reinstall with "
                "the plugin, or install the Microsoft Visual C++ Redistributable (https://aka.ms/vs/17/release/vc_redist.x64.exe)")
    return f"can't load ({type(e).__name__}: {e})"
