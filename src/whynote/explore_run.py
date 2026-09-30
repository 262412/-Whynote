"""Append-only exploration execution and resume, independent of formal evaluation gates."""

import contextlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .controlled_environment import runtime_hash
from .controlled_replay import source_hash
from .explore_inputs import file_hash
from .explore_prepare import now, read_plan, save_json
from .laya_manifest import SHA256
from .replay_laya import ReplayError, require
from .replay_runtime import model_lock, trusted_command
from .source_mapping import digest
from .task_reasons import CATALOG_SHA256

BUCKETS = ("suggested", "abstained", "technical_failure", "ineligible", "skipped", "interrupted", "not_started")
ERRORS = {"timeout", "model_load_failed", "route_failed", "invalid_response", "worker_failed", "uncaught_error"}


def append(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def rebuild(output, index_name="index.sqlite3"):
    """Derived index is replayed, never trusted over the durable journal."""
    db = sqlite3.connect(Path(output) / index_name)
    db.executescript(
        "DROP TABLE IF EXISTS attempts; CREATE TABLE attempts (input_id TEXT, scheme TEXT, "
        "attempt_id TEXT UNIQUE, bucket TEXT, elapsed_ms REAL, result TEXT, PRIMARY KEY(input_id,scheme));"
    )
    journal = Path(output) / "journal.jsonl"
    if journal.exists():
        with journal.open(encoding="utf-8") as stream:
            for line in stream:
                require(len(line.encode()) <= 262144 and line.endswith("\n"), "journal_damaged")
                row = json.loads(line)
                if row["event"] == "started":
                    db.execute(
                        "INSERT INTO attempts VALUES (?,?,?,NULL,NULL,NULL)",
                        (row["input_id"], row["scheme"], row["attempt_id"]),
                    )
                elif row["event"] in ("completed", "failed", "skipped", "interrupted"):
                    require(row["bucket"] in BUCKETS[:-1], "journal_invalid_bucket")
                    count = db.execute(
                        "UPDATE attempts SET bucket=?,elapsed_ms=?,result=? WHERE input_id=? AND scheme=? "
                        "AND attempt_id=? AND bucket IS NULL",
                        (
                            row["bucket"],
                            row.get("elapsed_ms"),
                            json.dumps(row),
                            row["input_id"],
                            row["scheme"],
                            row["attempt_id"],
                        ),
                    ).rowcount
                    require(count == 1, "duplicate_or_missing_attempt")
    db.commit()
    return db


def classify(response, item, scheme):
    if not isinstance(response, dict):
        return {"bucket": "technical_failure", "error": "invalid_response"}
    if set(response) == {"error"}:
        code = response["error"]
        return {
            "bucket": "technical_failure",
            "error": code if isinstance(code, str) and code in ERRORS else "invalid_response",
        }
    require(
        all(
            response.get(k) == v
            for k, v in {"input_id": item["input_id"], "state_sha256": item["state_sha256"], "scheme": scheme}.items()
        ),
        "worker_identity_changed",
    )
    require(response.get("bucket") in BUCKETS[:4], "worker_invalid_bucket")
    # Copy only the declared schema, so unexpected worker fields never enter a report.
    allowed = {
        "input_id",
        "state_sha256",
        "scheme",
        "bucket",
        "prediction",
        "measurement",
        "error",
        "elapsed_ms",
        "metrics",
    }
    require(set(response) <= allowed, "worker_unexpected_fields")
    error = response.get("error")
    if error is not None and (not isinstance(error, str) or error not in ERRORS | {"input_budget_exceeded"}):
        return {"bucket": "technical_failure", "error": "invalid_response"}
    return response


def execute(
    output,
    factory,
    fingerprint,
    *,
    resume=False,
    guard=lambda: None,
    admission_guard=lambda: None,
    cancelled=lambda: False,
    progress=print,
):
    output = Path(output)
    plan = read_plan(output)
    runtime_path = output / "runtime.json"
    if resume:
        require(runtime_path.exists(), "run_not_started")
        require(json.loads(runtime_path.read_text(encoding="utf-8")) == fingerprint, "resume_fingerprint_changed")
    else:
        require(not runtime_path.exists() and not (output / "journal.jsonl").exists(), "run_already_started")
        save_json(runtime_path, fingerprint, exclusive=True)
    journal, exposures = output / "journal.jsonl", output / "exposure.jsonl"
    db = rebuild(output)
    inputs = sqlite3.connect(f"file:{(output / 'inputs.sqlite3').as_posix()}?mode=ro", uri=True)
    segment = uuid.uuid4().hex
    common = {"run_id": plan["run_id"], "segment_id": segment}
    start, error, status = time.perf_counter(), None, "COMPLETED"
    append(journal, common | {"event": "segment_started", "at": now(), "fingerprint": digest(fingerprint)})
    try:
        for input_id, scheme, attempt in db.execute(
            "SELECT input_id,scheme,attempt_id FROM attempts WHERE bucket IS NULL"
        ).fetchall():
            row = common | {
                "event": "interrupted",
                "input_id": input_id,
                "scheme": scheme,
                "attempt_id": attempt,
                "bucket": "interrupted",
                "error": "previous_result_unknown",
                "elapsed_ms": None,
            }
            append(journal, row)
            db.execute(
                "UPDATE attempts SET bucket='interrupted',result=? WHERE attempt_id=?", (json.dumps(row), attempt)
            )
        db.commit()
        with factory() as worker:
            worker.segment_id = segment
            for input_id, group_id, excluded in inputs.execute(
                "SELECT input_id,group_id,excluded FROM inputs ORDER BY ordinal"
            ):
                item = None
                for scheme in plan["schemes"]:
                    if db.execute(
                        "SELECT 1 FROM attempts WHERE input_id=? AND scheme=?", (input_id, scheme)
                    ).fetchone():
                        continue
                    if cancelled():
                        raise KeyboardInterrupt
                    guard()
                    admission_guard()
                    if item is None:
                        raw = inputs.execute("SELECT payload FROM inputs WHERE input_id=?", (input_id,)).fetchone()[0]
                        item = json.loads(raw)
                    attempt = uuid.uuid4().hex
                    identity = common | {"input_id": input_id, "scheme": scheme, "attempt_id": attempt}
                    append(journal, identity | {"event": "started", "at": now()})
                    db.execute("INSERT INTO attempts VALUES (?,?,?,NULL,NULL,NULL)", (input_id, scheme, attempt))
                    db.commit()
                    before = time.perf_counter()
                    try:
                        if excluded:
                            result = {"bucket": "skipped", "error": excluded}
                        elif len(item["state"].encode()) > 1048576:
                            result = {"bucket": "ineligible", "error": "input_budget_exceeded", "measurement": None}
                        else:
                            append(
                                exposures,
                                {
                                    "event": "model_exposure",
                                    "run_id": plan["run_id"],
                                    "input_id": input_id,
                                    "group_id": group_id,
                                    "context_group_id": item["conversation_group_id"],
                                    "at": now(),
                                },
                            )
                            payload = {k: item[k] for k in ("input_id", "state", "evidence_kinds")} | {"scheme": scheme}
                            try:
                                admission_guard()
                                response = worker.invoke(payload)
                            except ReplayError as exc:
                                if str(exc) == "source_expired":
                                    raise
                                require(str(exc) in ERRORS, str(exc))
                                response = {"error": str(exc)}
                            result = classify(response, item, scheme)
                        guard()
                    except KeyboardInterrupt:
                        result = {"bucket": "interrupted", "error": "cancelled_result_unknown"}
                    except ReplayError as exc:
                        result = (
                            {"bucket": "skipped", "error": "source_expired"}
                            if str(exc) == "source_expired"
                            else {"bucket": "technical_failure", "error": "uncaught_error"}
                        )
                    except BaseException:
                        result = {"bucket": "technical_failure", "error": "uncaught_error"}
                    elapsed = (time.perf_counter() - before) * 1000
                    event = {"skipped": "skipped", "interrupted": "interrupted", "technical_failure": "failed"}.get(
                        result["bucket"], "completed"
                    )
                    row = (
                        result
                        | identity
                        | {
                            "event": event,
                            "worker_elapsed_ms": result.get("elapsed_ms"),
                            "elapsed_ms": elapsed,
                            "at": now(),
                        }
                    )
                    append(journal, row)
                    db.execute(
                        "UPDATE attempts SET bucket=?,elapsed_ms=?,result=? WHERE attempt_id=?",
                        (result["bucket"], elapsed, json.dumps(row), attempt),
                    )
                    db.commit()
                    count = db.execute("SELECT COUNT(*) FROM attempts WHERE bucket IS NOT NULL").fetchone()[0]
                    progress(
                        json.dumps(
                            {
                                "completed_slots": count,
                                "planned_slots": plan["planned_slots"],
                                "source": item["source"],
                                "bucket": result["bucket"],
                            }
                        )
                    )
                    require(result.get("error") != "uncaught_error", "uncaught_error")
                    if result["bucket"] == "interrupted":
                        raise KeyboardInterrupt
                    require(result.get("error") != "source_expired", "source_expired")
                    admission_guard()
            guard()
    except KeyboardInterrupt:
        error, status = "cancelled", "STOPPED"
    except ReplayError as exc:
        error, status = ("source_expired" if str(exc) == "source_expired" else "uncaught_error"), "STOPPED"
    except Exception:
        error, status = "uncaught_error", "STOPPED"
    finally:
        append(
            journal,
            common
            | {
                "event": "segment_stopped" if error else "segment_completed",
                "status": status,
                "error": error,
                "elapsed_ms": (time.perf_counter() - start) * 1000,
                "at": now(),
            },
        )
        db.close()
        inputs.close()
    return {"status": status, "quality": "NOT_EVALUATED", "error": error}


class Worker:
    def __init__(self, box, config, record, cancelled):
        self.box, self.config, self.record, self.cancelled = box, config, record, cancelled
        self.session = None
        self.admission_guard = lambda: None

    def close(self):
        if self.session:
            session, self.session = self.session, None
            session.close()

    def invoke(self, payload):
        from .windows_session import ResidentSession

        try:
            self.admission_guard()
            if self.session is None:
                command = trusted_command(
                    self.config["python"],
                    "whynote.explore_worker",
                    "--model-dir",
                    self.config["model_dir"],
                    "--device",
                    self.config["device"],
                )
                before = time.perf_counter()
                self.session = ResidentSession(self.box, command)
                ready = json.loads(self.session.receive(self.config["load_timeout"], self.cancelled))
                if "error" in ready:
                    code = ready["error"]
                    self.close()
                    return {"error": code}
                require(
                    ready.get("kind") == "ready" and ready.get("source_sha256") == source_hash(),
                    "worker_source_changed",
                )
                self.record(
                    {
                        "event": "model_loaded",
                        "load_ms": (time.perf_counter() - before) * 1000,
                        "metrics": ready["metrics"],
                        "segment_id": self.segment_id,
                    }
                )
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.admission_guard()
            raw = self.session.request(encoded, self.config["request_timeout"], self.cancelled)
            result = json.loads(raw)
            if result.get("error") in ("worker_failed", "uncaught_error"):
                self.close()
            return result
        except BaseException:
            self.close()
            raise


def real_run(output, config, *, resume=False):
    from .windows_isolation import isolated_profile

    output = Path(output).resolve(strict=True)
    plan = read_plan(output)
    require(
        set(config) == {"python", "base_python", "model_dir", "device", "load_timeout", "request_timeout"},
        "invalid_run_config",
    )
    require(
        config["device"] in ("cpu", "cuda")
        and all(type(config[k]) in (int, float) and 0 < config[k] <= 600 for k in ("load_timeout", "request_timeout")),
        "invalid_run_config",
    )
    config = config | {k: str(Path(config[k]).resolve(strict=True)) for k in ("python", "base_python", "model_dir")}
    with model_lock():
        for source in plan["sources"]:
            require(
                datetime_valid(source["expires_at"]) and file_hash(source["path"]) == source["sha256"],
                "source_changed_or_expired",
            )
        for name, expected in SHA256.items():
            require(file_hash(Path(config["model_dir"]) / name) == expected, "model_hash_changed")
        code = source_hash()
        fingerprint = {
            "source_sha256": code,
            "runtime_sha256": runtime_hash(config["python"], config["base_python"]),
            "model": SHA256,
            "catalog": CATALOG_SHA256,
            "config": config,
            "manifest_sha256": file_hash(output / "manifest.json"),
            "inputs_sha256": plan["inputs_sha256"],
        }
        watched = [Path(s["path"]) for s in plan["sources"]] + [output / "inputs.sqlite3", output / "manifest.json"]
        stats = [(p.stat().st_size, p.stat().st_mtime_ns) for p in watched]

        def guard():
            require(
                source_hash() == code
                and all((p.stat().st_size, p.stat().st_mtime_ns) == s for p, s in zip(watched, stats, strict=True)),
                "run_fingerprint_changed",
            )

        def admission_guard():
            require(all(datetime_valid(source["expires_at"]) for source in plan["sources"]), "source_expired")

        cancel_file = output / "cancel.request"

        @contextlib.contextmanager
        def factory():
            scratch = output / ("sandbox-" + uuid.uuid4().hex)
            scratch.mkdir()
            roots = [
                Path(__file__).parent.parent,
                Path(config["python"]).parents[1],
                config["base_python"],
                config["model_dir"],
            ]
            with isolated_profile(roots, scratch) as box:
                worker = Worker(
                    box,
                    config,
                    lambda row: append(output / "journal.jsonl", row | {"run_id": plan["run_id"], "at": now()}),
                    cancel_file.exists,
                )
                worker.admission_guard = admission_guard
                try:
                    yield worker
                finally:
                    worker.close()

        admission_guard()
        result = execute(
            output,
            factory,
            fingerprint,
            resume=resume,
            guard=guard,
            admission_guard=admission_guard,
            cancelled=cancel_file.exists,
        )
        require(
            runtime_hash(config["python"], config["base_python"]) == fingerprint["runtime_sha256"],
            "runtime_changed_during_run",
        )
        return result


def datetime_valid(value):
    from datetime import UTC, datetime

    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.tzinfo is not None and stamp > datetime.now(UTC)
