"""Windows AppContainer without network capabilities, plus kill-on-close Job.

Read grants are scoped to a fresh profile SID and removed on context exit.
The worker receives context through stdin; it is never granted source/label directories.
"""

import contextlib
import ctypes as c
import os
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path

from .replay_laya import require


class STARTUPINFO(c.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        *[(name, w.DWORD) for name in ("x", "y", "xs", "ys", "xc", "yc", "fill", "flags")],
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_ptr", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class STARTUPINFOEX(c.Structure):
    _fields_ = [("startup", STARTUPINFO), ("attributes", c.c_void_p)]


class PROCESSINFO(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class CAPABILITIES(c.Structure):
    _fields_ = [("sid", c.c_void_p), ("capabilities", c.c_void_p), ("count", w.DWORD), ("reserved", w.DWORD)]


class JOBLIMIT(c.Structure):
    _fields_ = [
        ("process_time", c.c_longlong),
        ("job_time", c.c_longlong),
        ("flags", w.DWORD),
        ("min_working", c.c_size_t),
        ("max_working", c.c_size_t),
        ("active", w.DWORD),
        ("affinity", c.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class JOBEXTENDED(c.Structure):
    _fields_ = [
        ("basic", JOBLIMIT),
        ("io", c.c_ulonglong * 6),
        ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t),
        ("peak_process", c.c_size_t),
        ("peak_job", c.c_size_t),
    ]


def api():
    require(sys.platform == "win32", "appcontainer_unavailable")
    kernel = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "InitializeProcThreadAttributeList": ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
        "UpdateProcThreadAttribute": (
            [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p],
            w.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([c.c_void_p], None),
        "CreateProcessW": (
            [
                w.LPCWSTR,
                w.LPWSTR,
                c.c_void_p,
                c.c_void_p,
                w.BOOL,
                w.DWORD,
                c.c_void_p,
                w.LPCWSTR,
                c.POINTER(STARTUPINFOEX),
                c.POINTER(PROCESSINFO),
            ],
            w.BOOL,
        ),
        "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "LocalFree": ([c.c_void_p], c.c_void_p),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = args, result
    user = c.WinDLL("userenv", use_last_error=True)
    user.CreateAppContainerProfile.argtypes = [
        w.LPCWSTR,
        w.LPCWSTR,
        w.LPCWSTR,
        c.c_void_p,
        w.DWORD,
        c.POINTER(c.c_void_p),
    ]
    user.CreateAppContainerProfile.restype = c.c_long
    user.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    user.DeleteAppContainerProfile.restype = c.c_long
    security = c.WinDLL("advapi32", use_last_error=True)
    security.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.restype = w.BOOL
    security.FreeSid.argtypes = [c.c_void_p]
    return kernel, user, security


def acl(path, *arguments):
    result = subprocess.run(
        [str(Path(os.environ["SYSTEMROOT"]) / "System32/icacls.exe"), str(path), *arguments],
        capture_output=True,
        creationflags=0x08000000,
    )
    require(result.returncode == 0, "appcontainer_acl_failed")


@contextlib.contextmanager
def isolated_profile(read_roots, scratch):
    kernel, user, security = api()
    scratch = Path(scratch).resolve(strict=True)
    require(scratch.is_dir() and not any(scratch.iterdir()), "sandbox_scratch_not_empty")
    name = "Whynote.M54A." + uuid.uuid4().hex
    sid = c.c_void_p()
    require(
        user.CreateAppContainerProfile(name, name, "Whynote offline replay", None, 0, c.byref(sid)) == 0,
        "appcontainer_profile_failed",
    )
    sid_text = w.LPWSTR()
    granted = []
    try:
        require(security.ConvertSidToStringSidW(sid, c.byref(sid_text)), "appcontainer_sid_failed")
        value = sid_text.value
        kernel.LocalFree(sid_text)
        for root in sorted({Path(p).resolve(strict=True) for p in read_roots}):
            require(root.is_dir(), "invalid_sandbox_root")
            granted.append(root)
            acl(root, "/grant", f"*{value}:(OI)(CI)RX")
        # Read-only cwd/cache: preinstalled runtime must not need downloads or compilation.
        granted.append(scratch)
        acl(scratch, "/grant", f"*{value}:(OI)(CI)RX")
        yield WindowsSandbox(sid, scratch, kernel)
    finally:
        failures = []
        for root in reversed(granted):
            try:
                acl(root, "/remove:g", "*" + value)
            except Exception:
                failures.append("acl")
        if user.DeleteAppContainerProfile(name) != 0:
            failures.append("profile")
        security.FreeSid(sid)
        require(not failures, "appcontainer_cleanup_failed")


class WindowsSandbox:
    def __init__(self, sid, scratch, kernel):
        self.sid, self.scratch, self.kernel = sid, scratch, kernel

    def run(self, command, payload, timeout=60):
        import msvcrt

        require(0 < timeout <= 60 and len(payload) <= 1048576, "invalid_worker_budget")
        k = self.kernel
        started = time.perf_counter()
        in_read, in_write = os.pipe()
        out_read, out_write = os.pipe()
        null = os.open(os.devnull, os.O_WRONLY)
        child_fds = [in_read, out_write, null]
        for fd in child_fds:
            os.set_inheritable(fd, True)
        handles = (w.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in child_fds))
        size = c.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        info, process = STARTUPINFOEX(), PROCESSINFO()
        info.startup.cb = c.sizeof(info)
        info.startup.flags = 0x100
        info.startup.stdin, info.startup.stdout, info.startup.stderr = handles
        info.attributes = c.cast(attributes, c.c_void_p)
        job = k.CreateJobObjectW(None, None)
        initialized = False
        reader = writer = None
        output = bytearray()
        try:
            require(job, "sandbox_job_failed")
            limits = JOBEXTENDED()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            require(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)), "sandbox_job_failed")
            require(k.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)), "sandbox_attributes_failed")
            initialized = True
            capabilities = CAPABILITIES(self.sid, None, 0, 0)
            require(
                k.UpdateProcThreadAttribute(
                    attributes, 0, 0x20009, c.byref(capabilities), c.sizeof(capabilities), None, None
                ),
                "sandbox_capabilities_failed",
            )
            require(
                k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, c.sizeof(handles), None, None),
                "sandbox_handles_failed",
            )
            env = {
                key: os.environ[key]
                for key in ("SYSTEMROOT", "WINDIR", "USERPROFILE", "ALLUSERSPROFILE", "ProgramData", "PUBLIC")
                if key in os.environ
            }
            env.update(
                TEMP=str(self.scratch),
                TMP=str(self.scratch),
                HF_HOME=str(self.scratch),
                USERNAME="whynote",
                TORCHDYNAMO_DISABLE="1",
                TORCHINDUCTOR_CACHE_DIR=".",
                APPDATA=str(self.scratch),
                LOCALAPPDATA=str(self.scratch),
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1",
                TOKENIZERS_PARALLELISM="false",
                USE_TF="0",
            )
            block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0")
            command_line = c.create_unicode_buffer(subprocess.list2cmdline(command))
            created = k.CreateProcessW(
                command[0],
                command_line,
                None,
                None,
                True,
                0x08000000 | 0x80000 | 0x400 | 4,
                block,
                str(self.scratch),
                c.byref(info),
                c.byref(process),
            )
            self.last_os_error = c.get_last_error() if not created else 0
            require(created, "sandbox_spawn_failed")
            self.last_pid = process.pid
            require(k.AssignProcessToJobObject(job, process.process), "sandbox_job_attach_failed")
            for fd in child_fds:
                os.close(fd)
            child_fds.clear()

            def write_input():
                try:
                    with os.fdopen(in_write, "wb") as stream:
                        stream.write(payload)
                except (OSError, BrokenPipeError):
                    pass

            def read_output():
                with os.fdopen(out_read, "rb") as stream:
                    while chunk := stream.read(4096):
                        output.extend(chunk)
                        if len(output) > 131072:
                            k.TerminateJobObject(job, 1)
                            break

            writer, reader = threading.Thread(target=write_input), threading.Thread(target=read_output)
            writer.start()
            reader.start()
            require(k.ResumeThread(process.thread) != 0xFFFFFFFF, "sandbox_resume_failed")
            remaining = max(0, timeout - (time.perf_counter() - started))
            require(k.WaitForSingleObject(process.process, int(remaining * 1000)) == 0, "timeout")
            code = w.DWORD()
            require(k.GetExitCodeProcess(process.process, c.byref(code)) and code.value == 0, "worker_failed")
        finally:
            if process.process:
                k.TerminateProcess(process.process, 1)
            if job:
                k.TerminateJobObject(job, 1)
                accounting = (c.c_ulonglong * 6)()
                deadline = time.perf_counter() + 5
                self.cleanup_verified = False
                while time.perf_counter() < deadline:
                    if k.QueryInformationJobObject(job, 1, c.byref(accounting), c.sizeof(accounting), None):
                        active = c.cast(c.byref(accounting, 40), c.POINTER(w.DWORD)).contents.value
                        if active == 0:
                            self.cleanup_verified = True
                            break
                    time.sleep(0.01)
                k.CloseHandle(job)
            if process.process:
                k.WaitForSingleObject(process.process, 5000)
                k.CloseHandle(process.process)
                k.CloseHandle(process.thread)
            for fd in child_fds:
                os.close(fd)
            if reader:
                reader.join(5)
                writer.join(5)
                require(not reader.is_alive() and not writer.is_alive(), "sandbox_cleanup_failed")
            else:
                os.close(in_write)
                os.close(out_read)
            if initialized:
                k.DeleteProcThreadAttributeList(attributes)
            require(not job or self.cleanup_verified, "sandbox_cleanup_failed")
        require(len(output) <= 131072, "worker_output_exceeded")
        return bytes(output), (time.perf_counter() - started) * 1000
