"""Controlled TCP/UDP receivers distinguish send acceptance from packet delivery."""

import contextlib
import json
import socket

from .replay_laya import require
from .replay_runtime import plain_synthetic_process, trusted_command


def probe_network(sandbox, python):
    with contextlib.ExitStack() as stack:
        listeners = {}
        for version, family, address in (("v4", socket.AF_INET, "127.0.0.1"), ("v6", socket.AF_INET6, "::1")):
            for protocol, kind in (("tcp", socket.SOCK_STREAM), ("udp", socket.SOCK_DGRAM)):
                stream = stack.enter_context(socket.socket(family, kind))
                stream.bind((address, 0))
                if protocol == "tcp":
                    stream.listen(4)
                stream.settimeout(0.3)
                listeners[f"{protocol}_{version}"] = stream
        payload = json.dumps({name: stream.getsockname()[1] for name, stream in listeners.items()}).encode()
        command = trusted_command(python, "whynote.runtime_probe")

        def received():
            result = {}
            for name, stream in listeners.items():
                try:
                    if name.startswith("tcp"):
                        connection, _ = stream.accept()
                        connection.close()
                    else:
                        stream.recvfrom(128)
                    result[name] = True
                except TimeoutError:
                    result[name] = False
            return result

        control, _ = plain_synthetic_process(command, payload)
        require(not json.loads(control)["appcontainer"], "invalid_probe_control")
        positive = received()
        require(all(positive.values()), "probe_receiver_unavailable")
        output, _ = sandbox.run(command, payload)
        token = json.loads(output)
        negative = received()
        passed = (
            token["appcontainer"] is True
            and token["capability_count"] == 0
            and token["network_errno"]["tcp_documentation_address"] == 10013
            and not any(negative.values())
        )
        return {
            "schema_version": "m54a-network-probe-v1",
            "passed": passed,
            "positive_delivery": positive,
            "sandbox_delivery": negative,
            "token": token,
            "scope": "zero_network_capabilities_and_local_tcp_udp_delivery;external_udp_not_observed",
        }
