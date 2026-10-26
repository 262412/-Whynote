import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_two_stage_batch import bundle as bundle

from whynote import two_stage_batch as batch
from whynote import two_stage_research_data as data
from whynote import two_stage_review as review
from whynote import two_stage_review_ui as ui
from whynote import two_stage_v3 as v3


@pytest.fixture
def workspace(tmp_path):
    value = object.__new__(ui.Workspace)
    value.path = tmp_path
    value.catalog = v3.load_catalog()
    value.sample_sha256 = "sample"
    value.check = lambda: None
    case = {
        "target_id": "case-a",
        "group_id": "group-a",
        "input_version": "v3",
        "input_sha256": "hash",
        "documents": {
            "d0": {
                "text": "A😀B\n<script>alert(1)</script>",
                "role": "user",
                "sha256": review.text_hash("A😀B\n<script>alert(1)</script>"),
            }
        },
        "source": "positive_signal",
        "model_candidates": ["general.incomplete"],
        "probability": 0.9,
    }
    value.rows = {"case-a": {"target_id": "case-a", "partition": "calibration", "selected": True}}
    value.sample = {"binding": {"source_expires_at": "2099-01-01T00:00:00+00:00"}}
    value.case = lambda target: (case, {})
    return value


def client(workspace):
    return TestClient(ui.create_app(workspace, "token", host="127.0.0.1:8765"), base_url="http://127.0.0.1:8765")


def test_blind_case_all_reasons_no_model_source_signals(workspace):
    response = client(workspace).get("/token/case/case-a?reviewer=human1")
    assert response.status_code == 200
    value = response.json()
    assert len(value["case"]["reasons"]) == 86
    assert all({"tasks", "domains"} <= reason.keys() for reason in value["case"]["reasons"])
    assert not {"probability", "model_candidates", "source"} & value["case"].keys()
    assert all(r["decision"] == "not_reviewed" for r in value["label"]["reasons"].values())
    assert "no-store" in response.headers["cache-control"]


def test_save_lock_export_import_and_second_reviewer_independence(workspace):
    app = client(workspace)
    label = app.get("/token/case/case-a?reviewer=human1").json()["label"]
    label.update(exposure="none_reported", independent_attestation=True)
    headers = {"Origin": "http://127.0.0.1:8765"}
    assert app.post("/token/save", json={"label": label}, headers=headers).status_code == 200
    locked = app.post("/token/save", json={"label": label, "lock": True}, headers=headers)
    assert locked.status_code == 200 and locked.json()["unreviewed_reasons"] == 86
    label["reasons"]["general.incomplete"]["basis"] = "changed after lock"
    assert app.post("/token/save", json={"label": label}, headers=headers).status_code == 409
    exported = app.get("/token/export?reviewer=human1").json()
    assert len(exported["labels"]) == 1
    assert workspace.import_labels(exported)["imported"] == 1
    fresh = app.get("/token/case/case-a?reviewer=human2").json()
    assert fresh["locked"] is False and fresh["label"]["reviewer"]["id"] == "human2"
    invalid = deepcopy(exported)
    invalid["labels"][0]["input_sha256"] = "changed"
    with pytest.raises(ValueError, match="binding_mismatch"):
        workspace.import_labels(invalid)


def test_same_origin_and_expiry_or_fingerprint_revocation(workspace):
    app = client(workspace)
    assert app.get("/token/overview", headers={"Host": "attacker.test"}).status_code == 403
    assert app.post("/token/save", json={}).status_code == 403
    assert app.post("/token/save", json={}, headers={"Origin": "http://other.test"}).status_code == 403
    workspace.check = lambda: (_ for _ in ()).throw(ValueError("expired"))
    assert app.get("/token/overview").status_code == 422


def test_locked_label_tampering_is_detected(workspace):
    label, _ = workspace.load_label("human", "initial", "case-a")
    label.update(exposure="none_reported", independent_attestation=True)
    workspace.save_label(label, lock=True)
    path = workspace.label_path("human", "initial", "case-a", "locked")
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["exposure"] = "predictions_seen"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError):
        workspace.load_label("human", "initial", "case-a")


def test_confirmation_cannot_be_opened_before_freeze(tmp_path):
    workspace = object.__new__(ui.Workspace)
    workspace.path = tmp_path
    workspace.check = lambda: None
    workspace.rows = {"case": {"selected": True, "partition": "confirmation"}}
    with pytest.raises(ValueError, match="freeze_policy"):
        workspace.case("case")


def test_real_sqlite_reader_can_load_save_and_navigate(bundle):
    workspace = object.__new__(ui.Workspace)
    workspace.path = bundle.root.parent / "ui"
    workspace.root = bundle.root
    workspace.source = bundle.plan
    workspace.catalog = v3.load_catalog()
    workspace.reader = None
    workspace.check = lambda: None
    workspace.sample_sha256 = "synthetic-sample"
    workspace.rows = {}
    gate = batch.Gate(bundle.root, bundle.plan, workspace.path, {}, None, "preview")
    with batch.Inputs(bundle.root, bundle.plan, gate) as reader:
        for target in (t for t in bundle.plan["targets"] if t["eligible"]):
            value, _, _ = data.original_input(reader, target)
            workspace.rows[target["target_id"]] = {
                "target_id": target["target_id"],
                "group_id": target["group_id"],
                "input_version": value["schema_version"],
                "input_sha256": value["input_sha256"],
                "partition": "calibration",
                "selected": True,
            }
    # A persistent server uses one event loop; retain real SQLite thread checks.
    with client(workspace) as app:
        for target_id in workspace.rows:
            path = f"/token/case/{target_id}?reviewer=synthetic-ai"
            response = app.get(path)
            assert response.status_code == 200
            label = response.json()["label"]
            label["reviewer"]["kind"] = "ai"
            saved = app.post("/token/save", json={"label": label}, headers={"Origin": "http://127.0.0.1:8765"})
            assert saved.status_code == 200
            assert saved.json()["unreviewed_reasons"] == 86
            assert app.get(path).json()["label"] == label
        app.portal.call(workspace.close)
    assert workspace.reader is None
