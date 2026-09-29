"""Independent ledger entry-point checks after switching live to fixture."""

import pytest
from test_live_suggestions import enabled as enabled
from test_suggestions import events, generate, render, respond
from test_suggestions import trial as trial
from test_template_suggestions import host as host

from whynote.domain import ConflictError


@pytest.mark.parametrize("callback", ["render", "respond"])
def test_inflight_live_ledger_callback_rejected_after_backend_rollback(enabled, callback):
    f = enabled
    target = f.action._target(f.chat_record, f.answer_record.body)
    f.config["suggestion_synthetic_targets"] = {target["object_id"]: target["object_version"]}
    f.store.config = f.config
    binding = generate(f)
    assert binding["model_source"] == "model_inferred_unconfirmed"
    if callback == "respond":
        render(f, binding)
    before = events(f)
    f.store.config = {**f.config, "suggestion_backend": "fixture"}
    rejected = False
    try:
        if callback == "render":
            render(f, binding)
        else:
            respond(f, name="after-rollback")
    except ConflictError:
        rejected = True
    assert events(f) == before, "Q-30: backend rollback appended an in-flight live callback"
    assert rejected, "Q-30: backend rollback accepted an in-flight live callback"
