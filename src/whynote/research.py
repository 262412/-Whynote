"""Server-controlled, synthetic-only S1 research registrations and answer ledger."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path

from .domain import MANUAL_UI_VERSION, ConflictError, NotFoundError, Principal

SOURCES = ("self_natural", "scripted", "public_replay")
REGISTRATION_FIELDS = {
    "registration_id",
    "study_ref",
    "protocol_ref",
    "context_ref",
    "feedback_ref",
    "source_kind",
    "public_label_origin",
}
BROWSER_FIELDS = REGISTRATION_FIELDS | {"research", "source", "actor_role", "research_enabled"}


def validate_config(config):
    enabled = config.get("research_enabled", False)
    if type(enabled) is not bool or (enabled and config.get("mode") != "mock"):
        raise ValueError("S1 research is available only for explicit mock development")
    if enabled:
        for name in ("research_study_ref", "research_protocol_ref"):
            if not isinstance(config.get(name), str):
                raise ValueError("Server research study and protocol references are required")
            uuid.UUID(config[name])
    versions = config.get("research_versions", {})
    if not isinstance(versions, dict) or set(versions) - {"host_sha", "patch_sha256", "config_ref", "model_revision"}:
        raise ValueError("Invalid research version fields")
    for key, value in versions.items():
        if value is None:
            continue
        pattern = {"host_sha": r"[0-9a-f]{40}", "patch_sha256": r"[0-9a-f]{64}"}.get(key, r"[a-zA-Z0-9_.:-]{1,128}")
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("Invalid research version value")
        if key == "config_ref":
            uuid.UUID(value)


def initialize(db):
    statements = [
        """CREATE TABLE IF NOT EXISTS s1_research_sources (
            registration_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
            previous_id TEXT, registered_at REAL NOT NULL, data TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS s1_research_attempts (
            attempt_id TEXT PRIMARY KEY REFERENCES s1_generations(attempt_id),
            registration_id TEXT REFERENCES s1_research_sources(registration_id), versions TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS s1_research_states (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id TEXT NOT NULL REFERENCES s1_research_attempts(attempt_id),
            kind TEXT NOT NULL, recorded_at REAL NOT NULL, UNIQUE(attempt_id, kind)
        )""",
        """CREATE TABLE IF NOT EXISTS s1_research_answers (
            attempt_id TEXT PRIMARY KEY REFERENCES s1_research_attempts(attempt_id),
            object_version TEXT NOT NULL, eligible_at REAL NOT NULL
        )""",
    ]
    for statement in statements:
        db.execute(statement)
    for table in ("sources", "attempts", "states", "answers"):
        for operation in ("UPDATE", "DELETE"):
            db.execute(
                f"CREATE TRIGGER IF NOT EXISTS research_{table}_no_{operation.lower()} "
                f"BEFORE {operation} ON s1_research_{table} "
                "BEGIN SELECT RAISE(ABORT, 'research records are append-only'); END"
            )


def register_source(store, principal: Principal, chat_id: str, registration: dict, expected_registration_id=None):
    """Local operator entry; never expose this function as a browser write route."""
    config = store.config
    validate_config(config)
    if config.get("research_enabled") is not True or principal != Principal(config["instance_id"], config["user_id"]):
        raise NotFoundError("Research registration is unavailable")
    if not isinstance(registration, dict) or set(registration) != REGISTRATION_FIELDS:
        raise ValueError("Invalid research registration fields")
    data = dict(registration)
    for key in ("registration_id", "study_ref", "protocol_ref", "context_ref", "feedback_ref"):
        if key == "feedback_ref" and data[key] is None:
            continue
        if not isinstance(data[key], str):
            raise ValueError("Research references must be UUIDs")
        data[key] = str(uuid.UUID(data[key]))
    source = data["source_kind"]
    if source not in SOURCES or data["public_label_origin"] not in ("none", "original_user", "evaluator", "automated"):
        raise ValueError("Invalid research source")
    if source != "public_replay" and data["public_label_origin"] != "none":
        raise ValueError("Public label origin requires public_replay")
    data["actor_role"] = "evaluator" if source == "public_replay" else "self_report"
    if (data["study_ref"], data["protocol_ref"], data["context_ref"]) != (
        str(uuid.UUID(config["research_study_ref"])),
        str(uuid.UUID(config["research_protocol_ref"])),
        chat_id,
    ):
        raise ValueError("Research references do not match the server study, protocol or chat")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with store._transaction() as db:
        store._admitted(db, chat_id, principal.actor_ref)
        if data["feedback_ref"] is not None:
            feedback = db.execute(
                "SELECT target_ref FROM actions WHERE event_id=? AND tenant_ref=? AND actor_ref=?",
                (data["feedback_ref"], principal.tenant_ref, principal.actor_ref),
            ).fetchone()
            if not feedback or json.loads(feedback[0]).get("object_id", "").split("/")[0] != chat_id:
                raise NotFoundError("Research feedback reference is unavailable")
        previous = db.execute(
            "SELECT * FROM s1_research_sources WHERE registration_id=?", (data["registration_id"],)
        ).fetchone()
        if previous:
            if (previous["chat_id"], previous["user_id"], previous["data"], previous["previous_id"]) != (
                chat_id,
                principal.actor_ref,
                encoded,
                expected_registration_id,
            ):
                raise ConflictError("Research registration ID conflicts")
            return data["registration_id"]
        latest = db.execute(
            "SELECT registration_id FROM s1_research_sources WHERE chat_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1",
            (chat_id, principal.actor_ref),
        ).fetchone()
        if (latest[0] if latest else None) != expected_registration_id:
            raise ConflictError("Research registration has changed")
        import time

        db.execute(
            "INSERT INTO s1_research_sources VALUES (?, ?, ?, ?, ?, ?)",
            (data["registration_id"], chat_id, principal.actor_ref, expected_registration_id, time.time(), encoded),
        )
    return data["registration_id"]


def capture_attempt(db, config, attempt_id):
    if config.get("research_enabled") is not True:
        return
    row = db.execute("SELECT * FROM s1_generations WHERE attempt_id=?", (attempt_id,)).fetchone()
    source = db.execute(
        "SELECT registration_id FROM s1_research_sources WHERE chat_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1",
        (row["chat_id"], row["user_id"]),
    ).fetchone()
    supplied = config.get("research_versions", {})
    versions = {k: supplied.get(k) for k in ("host_sha", "patch_sha256", "config_ref", "model_revision")}
    versions.update(
        mode=row["mode"],
        provider_model=row["provider_model"],
        pricing_version=row["pricing_version"],
        ui_version=MANUAL_UI_VERSION,
        contract="s1-research-v1",
    )
    db.execute(
        "INSERT INTO s1_research_attempts VALUES (?, ?, ?)",
        (attempt_id, source[0] if source else None, json.dumps(versions, sort_keys=True)),
    )
    record_state(db, attempt_id, "pending", row["created_at"])


def record_state(db, attempt_id, kind, timestamp):
    if db.execute("SELECT 1 FROM s1_research_attempts WHERE attempt_id=?", (attempt_id,)).fetchone():
        db.execute(
            "INSERT OR IGNORE INTO s1_research_states(attempt_id,kind,recorded_at) VALUES (?, ?, ?)",
            (attempt_id, kind, timestamp),
        )


def record_answer(db, attempt_id, version, timestamp):
    if db.execute("SELECT 1 FROM s1_research_attempts WHERE attempt_id=?", (attempt_id,)).fetchone():
        db.execute("INSERT OR IGNORE INTO s1_research_answers VALUES (?, ?, ?)", (attempt_id, version, timestamp))
        record_state(db, attempt_id, "eligible", timestamp)


def main():
    parser = argparse.ArgumentParser(description="Local operator: register an enrolled synthetic S1 chat")
    parser.add_argument("--config", required=True)
    parser.add_argument("--chat", required=True)
    parser.add_argument("--registration", required=True, help="Strict JSON reference manifest; no content")
    parser.add_argument("--expected-registration-id")
    args = parser.parse_args()
    from .s1 import TrialStore, load_config

    config = load_config(args.config)
    registration = json.loads(Path(args.registration).read_text(encoding="utf-8"))
    result = register_source(
        TrialStore(config),
        Principal(config["instance_id"], config["user_id"]),
        args.chat,
        registration,
        args.expected_registration_id,
    )
    print(json.dumps({"registration_id": result}))


if __name__ == "__main__":
    main()
