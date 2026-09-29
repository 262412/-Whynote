"""Bounded line IPC for one resident worker inside the existing Windows isolation."""

import ctypes as c
import os
import queue
import subprocess
import threading
import time
from ctypes import wintypes as w

from .replay_laya import require
from .windows_isolation import CAPABILITIES, JOBEXTENDED, PROCESSINFO, STARTUPINFOEX


class ResidentSession:
    def __init__(self, sandbox, command):
        self.box, self.k = sandbox, sandbox.kernel
        self.process, self.job = PROCESSINFO(), None
        self.fds, self.threads = [], []
        self.incoming, self.outgoing = queue.Queue(maxsize=2), queue.Queue(maxsize=1)
        self.closed = False
        try:
            self.start(command)
        except BaseException:
            self.close()
            raise

    def start(self, command):
        import msvcrt

        k = self.k
        in_read, in_write = os.pipe()
        out_read, out_write = os.pipe()
        null = os.open(os.devnull, os.O_WRONLY)
        self.fds = [in_read, in_write, out_read, out_write, null]
        for fd in (in_read, out_write, null):
            os.set_inheritable(fd, True)
        handles = (w.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in (in_read, out_write, null)))
        size = c.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
        attrs, info = c.create_string_buffer(size.value), STARTUPINFOEX()
        info.startup.cb, info.startup.flags = c.sizeof(info), 0x100
        info.startup.stdin, info.startup.stdout, info.startup.stderr = handles
        info.attributes = c.cast(attrs, c.c_void_p)
        self.job = k.CreateJobObjectW(None, None)
        require(self.job, "sandbox_job_failed")
        limits = JOBEXTENDED()
        limits.basic.flags = 0x2000
        require(k.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)), "sandbox_job_failed")
        require(k.InitializeProcThreadAttributeList(attrs, 2, 0, c.byref(size)), "sandbox_attributes_failed")
        try:
            capabilities = CAPABILITIES(self.box.sid, None, 0, 0)
            require(
                k.UpdateProcThreadAttribute(
                    attrs, 0, 0x20009, c.byref(capabilities), c.sizeof(capabilities), None, None
                ),
                "sandbox_capabilities_failed",
            )
            require(
                k.UpdateProcThreadAttribute(attrs, 0, 0x20002, handles, c.sizeof(handles), None, None),
                "sandbox_handles_failed",
            )
            env = {
                key: os.environ[key]
                for key in ("SYSTEMROOT", "WINDIR", "USERPROFILE", "ALLUSERSPROFILE", "ProgramData", "PUBLIC")
                if key in os.environ
            }
            env.update(
                {
                    key: str(self.box.scratch)
                    for key in ("TEMP", "TMP", "HF_HOME", "APPDATA", "LOCALAPPDATA", "TORCHINDUCTOR_CACHE_DIR")
                }
            )
            env.update(
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1",
                TOKENIZERS_PARALLELISM="false",
                TORCHDYNAMO_DISABLE="1",
                USE_TF="0",
                PYTHONUNBUFFERED="1",
            )
            block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0")
            line = c.create_unicode_buffer(subprocess.list2cmdline(command))
            require(
                k.CreateProcessW(
                    command[0],
                    line,
                    None,
                    None,
                    True,
                    0x08000000 | 0x80000 | 0x400 | 4,
                    block,
                    str(self.box.scratch),
                    c.byref(info),
                    c.byref(self.process),
                ),
                "sandbox_spawn_failed",
            )
            require(k.AssignProcessToJobObject(self.job, self.process.process), "sandbox_job_attach_failed")
        finally:
            k.DeleteProcThreadAttributeList(attrs)
        for fd in (in_read, out_write, null):
            os.close(fd)
            self.fds.remove(fd)

        def read():
            try:
                with os.fdopen(out_read, "rb") as stream:
                    while line := stream.readline(131073):
                        if len(line) > 131072 or not line.endswith(b"\n"):
                            self.incoming.put_nowait(None)
                            k.TerminateJobObject(self.job, 1)
                            return
                        self.incoming.put_nowait(line)
            except (OSError, queue.Full):
                k.TerminateJobObject(self.job, 1)
            finally:
                try:
                    self.incoming.put_nowait(None)
                except queue.Full:
                    pass

        def write():
            try:
                with os.fdopen(in_write, "wb") as stream:
                    while (payload := self.outgoing.get()) is not None:
                        stream.write(payload + b"\n")
                        stream.flush()
            except OSError:
                pass

        self.fds.remove(out_read)
        self.fds.remove(in_write)
        self.threads = [threading.Thread(target=read), threading.Thread(target=write)]
        for thread in self.threads:
            thread.start()
        require(k.ResumeThread(self.process.thread) != 0xFFFFFFFF, "sandbox_resume_failed")

    def receive(self, timeout=60, cancelled=lambda: False):
        require(0 < timeout <= 600, "invalid_worker_timeout")
        deadline = time.monotonic() + timeout
        while True:
            if cancelled():
                raise KeyboardInterrupt
            remaining = deadline - time.monotonic()
            require(remaining > 0, "timeout")
            try:
                value = self.incoming.get(timeout=min(0.1, remaining))
                require(value is not None, "worker_failed")
                return value
            except queue.Empty:
                continue

    def request(self, payload, timeout=60, cancelled=lambda: False):
        require(len(payload) <= 2 * 1048576 and b"\n" not in payload, "invalid_worker_payload")
        require(self.incoming.empty() and self.outgoing.empty(), "worker_protocol_error")
        self.outgoing.put_nowait(payload)
        return self.receive(timeout, cancelled)

    def close(self):
        if self.closed:
            return
        self.closed = True
        k = self.k
        self.box.cleanup_verified = not self.job
        if self.process.process:
            k.TerminateProcess(self.process.process, 1)
        if self.job:
            k.TerminateJobObject(self.job, 1)
            accounting = (c.c_ulonglong * 6)()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if k.QueryInformationJobObject(self.job, 1, c.byref(accounting), c.sizeof(accounting), None):
                    if c.cast(c.byref(accounting, 40), c.POINTER(w.DWORD)).contents.value == 0:
                        self.box.cleanup_verified = True
                        break
                time.sleep(0.01)
        try:
            self.outgoing.put_nowait(None)
        except queue.Full:
            # A blocked pipe write is released by terminating the job above.
            self.outgoing.get_nowait()
            self.outgoing.put_nowait(None)
        for thread in self.threads:
            thread.join(5)
        for fd in self.fds:
            os.close(fd)
        for handle in (self.process.process, self.process.thread, self.job):
            if handle:
                k.CloseHandle(handle)
        require(self.box.cleanup_verified and not any(t.is_alive() for t in self.threads), "sandbox_cleanup_failed")
