"""A local-only blind review entrypoint backed by append-only label imports."""

import json
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import two_stage_batch as batch
from . import two_stage_research_data as data
from . import two_stage_review as review
from . import two_stage_v3 as v3


class Workspace:
    def __init__(self, directory, project_root):
        self.path = Path(directory).resolve()
        self.project_root = Path(project_root).resolve()
        self.lock = batch.decode(batch.read(self.path / "workflow.lock.json"))
        self.root = Path(self.lock["data_root"])
        self.catalog = v3.load_catalog()
        self.check()
        self.sample = batch.decode(batch.read(self.path / "sample.manifest.json"))
        self.sample_sha256 = data.file_sha(self.path / "sample.manifest.json")
        self.rows = {r["target_id"]: r for r in self.sample["rows"]}
        self.reader = None

    def check(self):
        review.require(
            data.runtime(self.project_root) == self.lock["binding"]["runtime"], "frozen_input_or_inference_code_changed"
        )
        review.require(
            data.analysis_binding(self.project_root) == self.lock["binding"]["analysis"], "frozen_analysis_code_changed"
        )
        for name, pin in self.lock["files"].items():
            review.require(data.file_sha(self.path / name) == pin, "workflow_file_changed")
        self.source = batch.metadata(self.root)
        batch.source_admission(self.root, self.source)
        review.require(self.source["expires_at"] == self.lock["binding"]["source_expires_at"], "source_expiry_changed")

    def close(self):
        if self.reader is not None:
            self.reader.__exit__()
            self.reader = None

    def case(self, target_id):
        self.check()
        review.require(target_id in self.rows, "case_not_selected")
        row = self.rows[target_id]
        review.require(row["selected"], "case_not_selected")
        if row["partition"] == "confirmation":
            review.require(
                (self.path / "confirmation.freeze.json").is_file(), "freeze_policy_and_confirmation_plan_before_review"
            )
        if self.reader is None:
            gate = batch.Gate(self.root, self.source, self.path, {}, None, "preview")
            self.reader = batch.Inputs(self.root, self.source, gate).__enter__()
        target = next(t for t in self.source["targets"] if t["target_id"] == target_id)
        value, _, _ = data.original_input(self.reader, target)
        review.require(value["input_sha256"] == row["input_sha256"], "review_input_changed")
        route, visible_route, _ = v3.plan_route(value, self.catalog)
        return row | {
            "documents": value["documents"],
            "foundation": value["facts"],
            "materials": value["materials"],
            "route_input": {"scope": route["information_scope"], "documents": visible_route["documents"]},
        }, value

    def label_path(self, reviewer, round_id, target_id, kind):
        review.require(isinstance(reviewer, str) and reviewer.strip() and len(reviewer) <= 100, "reviewer_required")
        review.require(re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", round_id) is not None, "invalid_round_id")
        review.require(target_id in self.rows, "unknown_target")
        return self.path / "labels" / review.fingerprint([reviewer, round_id]) / (target_id + "." + kind + ".json")

    def load_label(self, reviewer, round_id, target_id):
        case, _ = self.case(target_id)
        locked = self.label_path(reviewer, round_id, target_id, "locked")
        draft = self.label_path(reviewer, round_id, target_id, "draft")
        if locked.exists():
            receipt = batch.decode(batch.read(locked.with_suffix(".receipt.json")))
            return batch.decode(batch.read(locked, expected=receipt["sha256"])), True
        if draft.exists():
            return batch.decode(batch.read(draft)), False
        return review.empty_label(case, self.catalog, reviewer, round_id=round_id), False

    def save_label(self, label, *, lock=False):
        case, _ = self.case(label["target_id"])
        review.validate_label(label, case, self.catalog)
        reviewer, round_id = label["reviewer"]["id"], label["round_id"]
        destination = self.label_path(reviewer, round_id, label["target_id"], "locked" if lock else "draft")
        review.require(
            not self.label_path(reviewer, round_id, label["target_id"], "locked").exists(),
            "initial_review_is_immutable",
        )
        if lock:
            review.require(
                label["exposure"] != "not_reported" and label["independent_attestation"], "review_attestation_required"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        batch.save(destination, label, new=lock)
        if lock:
            batch.save(
                destination.with_suffix(".receipt.json"),
                {"sha256": data.file_sha(destination), "locked_at": review.timestamp()},
                new=True,
            )
        return {
            "locked": lock,
            "label_sha256": review.fingerprint(label),
            "unreviewed_reasons": sum(r["decision"] == "not_reviewed" for r in label["reasons"].values()),
            "human_identity": "self_attested_not_externally_verified",
        }

    def export_labels(self, reviewer, round_id):
        self.check()
        rows = []
        for target in self.rows:
            path = self.label_path(reviewer, round_id, target, "locked")
            if path.exists():
                receipt = batch.decode(batch.read(path.with_suffix(".receipt.json")))
                rows.append(batch.decode(batch.read(path, expected=receipt["sha256"])))
        return {"schema_version": "jev-review-export-v1", "sample_sha256": self.sample_sha256, "labels": rows}

    def import_labels(self, payload):
        review.require(
            set(payload) == {"schema_version", "sample_sha256", "labels"}
            and payload["schema_version"] == "jev-review-export-v1",
            "invalid_import_contract",
        )
        review.require(payload["sample_sha256"] == self.sample_sha256, "import_sample_mismatch")
        labels = payload["labels"]
        review.require(isinstance(labels, list) and len(labels) <= len(self.rows) * 5, "invalid_import_count")
        destinations = []
        for label in labels:
            case, _ = self.case(label["target_id"])
            review.validate_label(label, case, self.catalog)
            destination = self.label_path(label["reviewer"]["id"], label["round_id"], label["target_id"], "locked")
            review.require(
                label["independent_attestation"] and label["exposure"] != "not_reported", "review_attestation_required"
            )
            if destination.exists():
                receipt = batch.decode(batch.read(destination.with_suffix(".receipt.json")))
                review.require(
                    batch.decode(batch.read(destination, expected=receipt["sha256"])) == label,
                    "cannot_replace_locked_opinion",
                )
            destinations.append((destination, label))
        review.require(len({str(path) for path, _ in destinations}) == len(destinations), "duplicate_review_identity")
        for destination, label in destinations:
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                batch.save(destination, label, new=True)
                batch.save(
                    destination.with_suffix(".receipt.json"),
                    {"sha256": data.file_sha(destination), "locked_at": review.timestamp()},
                    new=True,
                )
        return {"imported": len(labels), "formal_confirmation": "NOT_EXECUTED"}


def create_app(workspace, token, *, host):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    prefix = "/" + token

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("host") != host or not request.url.path.startswith(prefix + "/"):
            return JSONResponse({"error": "local_origin_required"}, status_code=403)
        if request.method == "POST" and request.headers.get("origin") != "http://" + host:
            return JSONResponse({"error": "same_origin_required"}, status_code=403)
        if int(request.headers.get("content-length", "0")) > 2 * 1024 * 1024:
            return JSONResponse({"error": "body_too_large"}, status_code=413)
        try:
            workspace.check()
            response = await call_next(request)
        except (ValueError, KeyError, TypeError, OSError):
            return JSONResponse({"error": "workflow_binding_or_request_invalid"}, status_code=422)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            }
        )
        return response

    @app.get(prefix + "/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).with_name("two_stage_review.html")).read_text(encoding="utf-8")

    @app.get(prefix + "/overview")
    def overview():
        return {
            "sample_sha256": workspace.sample_sha256,
            "expires_at": workspace.sample["binding"]["source_expires_at"],
            "confirmation_available": (workspace.path / "confirmation.freeze.json").exists(),
            "cases": [
                {"target_id": r["target_id"], "partition": r["partition"]}
                for r in workspace.rows.values()
                if r["selected"]
            ],
        }

    @app.get(prefix + "/case/{target_id}")
    async def case(target_id: str, reviewer: str, round_id: str = "initial"):
        # Keep the cached SQLite reader on the event-loop thread, as in save().
        try:
            value, _ = workspace.case(target_id)
            label, locked = workspace.load_label(reviewer, round_id, target_id)
            return {"case": review.blind_case(value, workspace.catalog), "label": label, "locked": locked}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post(prefix + "/save")
    async def save(request: Request):
        payload = await request.json()
        try:
            return workspace.save_label(payload["label"], lock=payload.get("lock") is True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get(prefix + "/export")
    def export(reviewer: str, round_id: str = "initial"):
        value = workspace.export_labels(reviewer, round_id)
        return Response(
            review.encoded(value),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="human-review.json"'},
        )

    return app


def serve(workspace, port):
    import socket

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(24)
    url = f"http://127.0.0.1:{port}/{token}/"
    batch.save(workspace.path / "server.json", {"url": url, "local_only": True, "label_state": "user_managed"})
    print(json.dumps({"url": url, "status": "READY_FOR_HUMAN_REVIEW"}), flush=True)
    app = create_app(workspace, token, host=f"127.0.0.1:{port}")
    try:
        uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(sockets=[sock])
    finally:
        workspace.close()
        sock.close()
