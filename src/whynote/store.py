"""SQLite implementation of the append-only event and transactional outbox contract."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .domain import (
    ConflictError,
    GateSignals,
    NotFoundError,
    Principal,
    REASON_CODES,
    decide_gate,
    project,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EventStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    event_id TEXT PRIMARY KEY,
                    tenant_ref TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL REFERENCES actions(event_id),
                    event_type TEXT NOT NULL,
                    event_version INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    source TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    tenant_ref TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_action ON events(event_id, seq);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TABLE IF NOT EXISTS idempotency (
                    tenant_ref TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_ref, actor_ref, key_hash)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    message_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES actions(event_id),
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    UNIQUE (event_id, topic)
                );
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _append(db: sqlite3.Connection, event_id: str, kind: str, payload: Mapping[str, Any], source: str) -> str:
        record_id = str(uuid.uuid4())
        tenant = db.execute("SELECT tenant_ref FROM actions WHERE event_id=?", (event_id,)).fetchone()
        if tenant is None:
            raise NotFoundError("feedback action not found")
        timestamp = _now()
        db.execute(
            "INSERT INTO events(record_id,event_id,event_type,event_version,schema_version,source,occurred_at,recorded_at,trace_id,tenant_ref,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, event_id, kind, 1, "1", source, timestamp, timestamp, str(uuid.uuid4()), tenant["tenant_ref"], _json(payload)),
        )
        return record_id

    @staticmethod
    def _events(db: sqlite3.Connection, event_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT record_id,event_type,event_version,schema_version,source,occurred_at,recorded_at,trace_id,tenant_ref,payload FROM events WHERE event_id=? ORDER BY seq",
            (event_id,),
        ).fetchall()
        return [
            {
                "record_id": row["record_id"],
                "event_type": row["event_type"],
                "event_version": row["event_version"],
                "schema_version": row["schema_version"],
                "source": row["source"],
                "occurred_at": row["occurred_at"],
                "recorded_at": row["recorded_at"],
                "trace_id": row["trace_id"],
                "tenant_ref": row["tenant_ref"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    @staticmethod
    def _check_idempotency(
        db: sqlite3.Connection, principal: Principal, key_hash: str, operation: str, request_hash: str
    ) -> sqlite3.Row | None:
        row = db.execute(
            "SELECT * FROM idempotency WHERE tenant_ref=? AND actor_ref=? AND key_hash=?",
            (principal.tenant_ref, principal.actor_ref, key_hash),
        ).fetchone()
        if row and (row["operation"] != operation or row["request_hash"] != request_hash):
            raise ConflictError("idempotency key reused with a different request")
        return row

    @staticmethod
    def _save_idempotency(
        db: sqlite3.Connection,
        principal: Principal,
        key_hash: str,
        operation: str,
        request_hash: str,
        event_id: str,
        record_id: str,
    ) -> None:
        db.execute(
            "INSERT INTO idempotency VALUES(?,?,?,?,?,?,?)",
            (principal.tenant_ref, principal.actor_ref, key_hash, operation, request_hash, event_id, record_id),
        )

    @staticmethod
    def _require_owner(db: sqlite3.Connection, principal: Principal, event_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM actions WHERE event_id=? AND tenant_ref=? AND actor_ref=?",
            (event_id, principal.tenant_ref, principal.actor_ref),
        ).fetchone()
        if row is None:
            raise NotFoundError("feedback action not found")
        return row

    def create_action(
        self, principal: Principal, target_ref: Mapping[str, str], metadata: Mapping[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        request_hash = _digest(_json({"target_ref": target_ref, "metadata": metadata}))
        key_hash = _digest(idempotency_key)
        with self._transaction() as db:
            old = self._check_idempotency(db, principal, key_hash, "create", request_hash)
            if old:
                return self._projection(db, old["event_id"])
            event_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO actions VALUES(?,?,?,?,?)",
                (event_id, principal.tenant_ref, principal.actor_ref, _json(target_ref), _now()),
            )
            record_id = self._append(
                db,
                event_id,
                "negative_feedback_action_recorded",
                {"target_ref": dict(target_ref), **metadata},
                "user",
            )
            db.execute(
                "INSERT INTO outbox(message_id,event_id,topic,payload,created_at) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), event_id, "feedback.gate.requested", _json({"event_id": event_id}), _now()),
            )
            self._save_idempotency(db, principal, key_hash, "create", request_hash, event_id, record_id)
            return self._projection(db, event_id)

    def _projection(self, db: sqlite3.Connection, event_id: str) -> dict[str, Any]:
        return {"event_id": event_id, **project(self._events(db, event_id))}

    def get_action(self, principal: Principal, event_id: str) -> dict[str, Any]:
        with self._transaction() as db:
            self._require_owner(db, principal, event_id)
            return self._projection(db, event_id)

    def get_events(self, principal: Principal, event_id: str) -> list[dict[str, Any]]:
        with self._transaction() as db:
            self._require_owner(db, principal, event_id)
            return self._events(db, event_id)

    def retract_action(self, principal: Principal, event_id: str, idempotency_key: str) -> dict[str, Any]:
        key_hash = _digest(idempotency_key)
        request_hash = _digest(_json({"event_id": event_id, "operation": "retract"}))
        with self._transaction() as db:
            self._require_owner(db, principal, event_id)
            old = self._check_idempotency(db, principal, key_hash, "retract", request_hash)
            if old:
                return self._projection(db, event_id)
            if self._projection(db, event_id)["action_status"] == "retracted":
                original = db.execute(
                    "SELECT record_id FROM events WHERE event_id=? AND event_type='action_retracted' ORDER BY seq LIMIT 1",
                    (event_id,),
                ).fetchone()
                self._save_idempotency(db, principal, key_hash, "retract", request_hash, event_id, original["record_id"])
                return self._projection(db, event_id)
            record_id = self._append(db, event_id, "action_retracted", {}, "user")
            db.execute(
                "INSERT INTO outbox(message_id,event_id,topic,payload,created_at) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), event_id, "feedback.action.retracted", _json({"event_id": event_id}), _now()),
            )
            self._save_idempotency(db, principal, key_hash, "retract", request_hash, event_id, record_id)
            return self._projection(db, event_id)

    def record_user_action(
        self,
        principal: Principal,
        event_id: str,
        user_action: str,
        reason_code: str | None,
        display_id: str | None,
        explicit_submission: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        allowed = {
            "reason_selected",
            "reason_confirmed",
            "reason_edited",
            "reason_declined",
            "reason_skipped",
            "attribution_invalidated",
        }
        if user_action not in allowed or not explicit_submission:
            raise ConflictError("an explicit supported user action is required")
        if user_action in {"reason_selected", "reason_confirmed", "reason_edited"}:
            if reason_code not in REASON_CODES:
                raise ConflictError("invalid reason code")
        elif reason_code is not None:
            raise ConflictError("this action must not include a reason code")
        payload = {"reason_code": reason_code, "display_id": display_id, "explicit_submission": True}
        request_hash = _digest(_json({"event_id": event_id, "user_action": user_action, **payload}))
        key_hash = _digest(idempotency_key)
        with self._transaction() as db:
            self._require_owner(db, principal, event_id)
            old = self._check_idempotency(db, principal, key_hash, user_action, request_hash)
            if old:
                return self._projection(db, event_id)
            state = self._projection(db, event_id)
            if state["action_status"] != "active":
                raise ConflictError("cannot attribute a retracted action")
            if user_action == "reason_confirmed" and (
                state["attribution_status"] != "model_inferred_unconfirmed" or state["reason_code"] != reason_code
            ):
                raise ConflictError("confirmation requires the currently shown suggestion")
            if user_action == "attribution_invalidated" and state["attribution_status"] != "model_inferred_unconfirmed":
                raise ConflictError("there is no active model suggestion to invalidate")
            record_id = self._append(db, event_id, user_action, payload, "user")
            self._save_idempotency(db, principal, key_hash, user_action, request_hash, event_id, record_id)
            return self._projection(db, event_id)

    def next_gate_message(self) -> dict[str, Any] | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT message_id,event_id FROM outbox WHERE topic='feedback.gate.requested' AND processed_at IS NULL ORDER BY created_at,message_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            action = db.execute("SELECT * FROM actions WHERE event_id=?", (row["event_id"],)).fetchone()
            return {
                "message_id": row["message_id"],
                "event_id": row["event_id"],
                "action_status": self._projection(db, row["event_id"])["action_status"],
                "tenant_ref": action["tenant_ref"],
                "actor_ref": action["actor_ref"],
                "target_ref": json.loads(action["target_ref"]),
            }

    def finish_gate_message(self, message_id: str, signals: GateSignals | None) -> dict[str, Any]:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE message_id=? AND topic='feedback.gate.requested'", (message_id,)).fetchone()
            if row is None:
                raise NotFoundError("gate message not found")
            event_id = row["event_id"]
            if row["processed_at"] is not None:
                return self._projection(db, event_id)
            state = self._projection(db, event_id)
            if state["action_status"] == "active" and state["gate"] is None:
                if signals is None:
                    raise ValueError("active actions require a gate decision")
                decision = decide_gate(signals)
                self._append(db, event_id, "inference_gate_decided", decision, "policy")
                if decision["gate_outcome"] == "allow":
                    # No approved snapshot, budget ledger or provider is wired yet.
                    self._append(db, event_id, "inference_abstained", {"reason": "pipeline_unconfigured"}, "system")
            db.execute("UPDATE outbox SET processed_at=? WHERE message_id=?", (_now(), message_id))
            return self._projection(db, event_id)


def process_one_gate(store: EventStore, evaluate: Callable[[dict[str, Any]], GateSignals]) -> dict[str, Any] | None:
    message = store.next_gate_message()
    if message is None:
        return None
    signals = evaluate(message) if message["action_status"] == "active" else None
    return store.finish_gate_message(message["message_id"], signals)
