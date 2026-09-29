"""A machine-wide model mutex and bounded child execution. No research authorization."""

import contextlib
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from .replay_laya import ReplayError, require


@contextlib.contextmanager
def model_lock():
    """Reject overlap before reading a prediction or allocating a model."""
    if sys.platform == "win32":
        from ctypes import wintypes as w

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        kernel.CreateMutexW.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.ReleaseMutex.argtypes = [w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateMutexW(None, False, "Global\\Whynote.M54A.Laya.Multilingual")
        require(bool(handle), "model_lock_unavailable")
        acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        try:
            require(acquired, "model_busy")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl

        path = Path("/tmp") / "whynote-m54a-laya.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ReplayError("model_busy") from None
            yield
        finally:
            os.close(fd)


def trusted_command(python, module, *args):
    python = Path(python).resolve(strict=True)
    package = str(Path(__file__).resolve().parents[1])
    require(module in ("whynote.controlled_worker", "whynote.runtime_probe"), "untrusted_worker")
    bootstrap = f"import sys; sys.path.insert(0, sys.argv.pop(1)); from {module} import main; main()"
    return [str(python), "-I", "-B", "-X", "utf8", "-c", bootstrap, package, *map(str, args)]


def plain_synthetic_process(command, payload, timeout=60):
    """Synthetic tests only. Real execution uses an OS-isolated launcher."""
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    try:
        stdout, _ = process.communicate(payload, timeout=timeout)
        require(process.returncode == 0 and len(stdout) <= 131072, "worker_failed")
        return stdout, (time.perf_counter() - started) * 1000
    except subprocess.TimeoutExpired:
        raise ReplayError("timeout") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
