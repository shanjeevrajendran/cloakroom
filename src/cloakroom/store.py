"""Files in Cloakroom's own folder: one writer at a time, and a file is either whole or not there."""
from __future__ import annotations

import contextlib
import os
import secrets
import time
from pathlib import Path


class Busy(OSError):
    """Another process held the lock for longer than the caller can wait."""


@contextlib.contextmanager
def locked(path: str | os.PathLike, seconds: float | None = None):
    """Hold path (a lock file, made when missing) until the block ends: one process and one thread at a time.
    seconds: the longest to wait for it, then Busy (a hook must answer before its agent stops waiting, and callers
    fail closed on the error); None waits as long as it takes. Windows: after 2 seconds of trying the block runs
    without the lock, as before."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            for _ in range(200):
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl
            if seconds is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                end = time.monotonic() + seconds
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= end:
                            raise Busy(f"{os.path.basename(path)} is held by another process") from None
                        time.sleep(0.01)
        yield
    finally:
        if os.name == "nt":
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def write_atomic(path: str | os.PathLike, data: bytes, mode: int = 0o600):
    """Put data at path in one step: a reader sees the old file or the new one, never half of one. The temporary
    file has a name of its own, so two writers never share one, and it is removed when the write fails."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
