"""Synthetic OS-network probe: no source data, labels, credentials or model."""

import ctypes as c
import json
import socket
import sys


def main():
    from ctypes import wintypes as w

    if sys.platform != "win32":
        print(json.dumps({"error": "appcontainer_unavailable"}))
        return
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    security.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    handle, needed, is_container = w.HANDLE(), w.DWORD(), w.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(handle)):
        print(json.dumps({"error": "token_probe_failed"}))
        return
    try:
        if not security.GetTokenInformation(handle, 29, c.byref(is_container), 4, c.byref(needed)):
            raise ValueError("token_probe_failed")
        security.GetTokenInformation(handle, 30, None, 0, c.byref(needed))
        buffer = c.create_string_buffer(needed.value)
        if not security.GetTokenInformation(handle, 30, buffer, needed.value, c.byref(needed)):
            raise ValueError("token_probe_failed")
        capabilities = c.cast(buffer, c.POINTER(w.DWORD)).contents.value
    finally:
        kernel.CloseHandle(handle)
    request = json.loads(sys.stdin.buffer.read(4096))
    checks = {}
    endpoints = [
        (
            name,
            socket.AF_INET6 if "v6" in name else socket.AF_INET,
            socket.SOCK_DGRAM if name.startswith("udp") else socket.SOCK_STREAM,
            ("::1" if "v6" in name else "127.0.0.1", port),
        )
        for name, port in request.items()
    ]
    endpoints.append(("tcp_documentation_address", socket.AF_INET, socket.SOCK_STREAM, ("192.0.2.1", 443)))
    for name, family, kind, address in endpoints:
        with socket.socket(family, kind) as stream:
            stream.settimeout(0.2)
            try:
                if kind == socket.SOCK_STREAM:
                    error = stream.connect_ex(address)
                else:
                    stream.sendto(b"WHYNOTE_SYNTHETIC_PROBE", address)
                    error = 0
            except OSError as exc:
                error = exc.errno
        checks[name] = error
    print(
        json.dumps(
            {"appcontainer": bool(is_container.value), "capability_count": capabilities, "network_errno": checks}
        )
    )
