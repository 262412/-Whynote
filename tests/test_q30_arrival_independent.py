"""An on-time callback cannot carry admission across a backend change."""

import hashlib
from pathlib import Path

import pytest
from test_live_rollback import ledger as ledger
from test_live_suggestions import enabled as enabled
from test_suggestions import events, generate, ref, render
from test_suggestions import trial as trial
from test_template_suggestions import host as host

from whynote import suggestions
from whynote.domain import ConflictError


@pytest.mark.parametrize("backend", ["fixture", "laya_local"])
@pytest.mark.parametrize("callback", ["render", "respond"])
@pytest.mark.parametrize("switch", [False, True])
def test_timely_arrival_rechecks_current_backend(ledger, backend, callback, switch):
    f = ledger
    f.config["suggestion_backend"] = backend
    binding = generate(f)
    if callback == "respond":
        render(f, binding)
    received_at = f.clock + 30
    f.clock += 65
    if switch:
        f.config["suggestion_backend"] = "fixture" if backend == "laya_local" else "laya_local"
    before = events(f)
    digest = hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest()

    def arrive():
        if callback == "render":
            return suggestions.render(
                f.store,
                f.principal,
                f.event_id,
                ref("late-render"),
                ref("display"),
                binding,
                server_received_at=received_at,
            )
        return suggestions.respond(
            f.store,
            f.principal,
            f.event_id,
            ref("late-response"),
            ref("one"),
            ref("display"),
            "yes",
            "code.behavior_changed",
            None,
            server_received_at=received_at,
        )

    if switch:
        with pytest.raises(ConflictError, match="versions"):
            arrive()
        assert events(f) == before
        assert hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest() == digest
    else:
        first = arrive()
        after = events(f)
        assert len(after) == len(before) + 1
        assert arrive() == first and events(f) == after
