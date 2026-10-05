"""Serial, local research batches over the fixed, admitted M55 input snapshot.

No production events, credential discovery, automatic retries or training exports.
The SQLite journal contains safe metadata only; source bodies stay in their existing store.
"""

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import two_stage as core
from .domain import NotFoundError
from .jev_provider import MODEL

VERSION = "jev-two-stage-batch-v1"
CONFIG_VERSION = "jev-two-stage-run-config-v1"
PLAN_SHA256 = "1bddf95e253a48a45531f3c5f85488f11ca10804e92a9e5bde617e5bdac7f990"
PREP = Path("var/research/typesafe-preflight/m55-full-20261004/prep")
RESEARCH = Path("var/research/typesafe-preflight")
SOURCES = {"helpsteer3", "wildfb", "wildfeedback"}
EXCLUSIONS = {
    "annotation_overlap",
    "conservative_byte_proxy_exceeded",
    "original_sensitive_pattern",
    "privacy_screening_unresolved",
}
CODE_FILES = (
    "src/whynote/__init__.py",
    "src/whynote/two_stage.py",
    "src/whynote/two_stage_batch.py",
    "src/whynote/jev_provider.py",
    "src/whynote/provider_keys.py",
    "src/whynote/domain.py",
    "src/whynote/two_stage_catalog.json",
    "scripts/start_two_stage.ps1",
)
OFFLINE_POLICY = core.Policy("synthetic-unapproved-v1", 0.8, 0.5, 0.8, 0.5)
MOCK_BUDGET = {
    "limit_usd": "1000000",
    "reserve_per_stage_usd": "1",
    "input_usd_per_million": "1",
    "output_usd_per_million": "0",
}


class Closed(ValueError):
    """Only fixed codes, never external exception text."""

    def __init__(self, code, *, missing=None):
        super().__init__(code)
        self.missing = missing


def require(condition, code):
    if not condition:
        raise Closed(code)


def encode(value):
    return core._encode(value, canonical=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=core._unique_object)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Closed("invalid_json") from None


def read(path, maximum=8 * 1024 * 1024, expected=None):
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "file_too_large")
    require(expected is None or sha(raw) == expected, "input_fingerprint_changed")
    return raw


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value), "invalid_identifier")
    return value


def unexpired(value):
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), "source_or_approval_expired")
        return expiry
    except (ValueError, TypeError, AttributeError):
        raise Closed("source_or_approval_expired") from None


def number(value, *, positive=False):
    try:
        require(type(value) in (str, int, float), "invalid_budget_number")
        result = Decimal(str(value))
        require(result.is_finite() and (result > 0 if positive else result >= 0), "invalid_budget_number")
        return result
    except (InvalidOperation, ValueError):
        raise Closed("invalid_budget_number") from None


def research_path(root, value):
    path = (root / value).resolve()
    require(path.is_relative_to(root / "var/research"), "input_path_outside_research")
    return path


def runtime_identity(root):
    """Check actual imports, then pin exactly the source used by this checkout."""
    root = Path(root).resolve()
    for name in ("__init__", "two_stage", "two_stage_batch", "jev_provider", "provider_keys", "domain"):
        module = importlib.import_module("whynote" if name == "__init__" else "whynote." + name)
        require(Path(module.__file__).resolve() == root / "src/whynote" / (name + ".py"), "wrong_module_origin")
    require(core.VERSION == "jev-two-stage-v1", "core_version_mismatch")
    catalog = core.load_catalog()
    pins = {name: sha(read(root / name, 2 * 1024 * 1024)) for name in CODE_FILES}
    return {
        "project_root": str(root),
        "module_file": str(Path(__file__).resolve()),
        "code_sha256": sha(encode(pins)),
        "code_pins": pins,
        "contract_version": core.VERSION,
        "catalog_version": catalog["version"],
        "catalog_sha256": core.CATALOG_SHA256,
        "stage_record_version": core.STAGE_RECORD_VERSION,
        "model": MODEL,
    }


def metadata(data_root):
    """No context or key reads: only pinned, body-free admission metadata."""
    plan = decode(read(data_root / PREP / "plan.json", expected=PLAN_SHA256))
    require(
        plan["schema_version"] == "m55-typesafe-offline-plan-v1"
        and plan["seed"] == 42
        and plan["target_count"] == 3000
        and plan["contract_id"] == "m55-original-choice-v1",
        "fixed_source_scope_changed",
    )
    require(
        Counter(t["source"] for t in plan["targets"]) == Counter(dict.fromkeys(SOURCES, 1000)), "source_counts_changed"
    )
    require(len({t["target_id"] for t in plan["targets"]}) == 3000, "duplicate_target")
    require({s["source"] for s in plan["sources"]} == SOURCES, "sources_changed")
    unexpired(plan["expires_at"])
    for target in plan["targets"]:
        identifier(target["target_id"])
        require(type(target["eligible"]) is bool, "invalid_eligibility")
        unexpired(target["expires_at"])
        if target["eligible"]:
            require(
                target["input_key"] == target["target_id"]
                and not target["modified"]
                and target["exclusion_code"] is None
                and not target["screening_flags"]
                and target["original_state_sha256"] == target["outbound_state_sha256"],
                "prepared_target_changed",
            )
        else:
            require(target["exclusion_code"] in EXCLUSIONS and target["input_key"] is None, "exclusion_changed")
    return plan


def source_admission(root, plan):
    entry = plan["input_pins"]["source_manifest"]
    manifest = decode(read(research_path(root, entry["path"]), expected=entry["sha256"]))
    require(
        Path(manifest["project_root"]).resolve() == root
        and Path(manifest["research_root"]).resolve() == root / "var/research",
        "source_storage_root_changed",
    )
    require(len(manifest["sources"]) == 3, "source_admission_changed")
    admitted = {item["source"]: item for item in manifest["sources"]}
    require(set(admitted) == SOURCES, "source_admission_changed")
    unexpired(plan["expires_at"])
    for source in plan["sources"]:
        item = admitted[source["source"]]
        require(
            item["status"] == "ADMITTED_FOR_EXPLORATION"
            and item["purpose"] == "local_unlabelled_exploration"
            and "local_codex" in item["access"]
            and item["max_targets"] == 1000
            and all(item[k] == source[k] for k in ("revision", "sha256", "expires_at", "bytes")),
            "source_admission_changed",
        )
        unexpired(item["expires_at"])


def configuration(path, mode, batch_id):
    config = decode(read(path)) if path else {"schema_version": CONFIG_VERSION, "policy": asdict(OFFLINE_POLICY)}
    require(isinstance(config, dict) and config.get("schema_version") == CONFIG_VERSION, "invalid_config_version")
    require(not set(config) - {"schema_version", "policy", "live"}, "invalid_config_fields")
    try:
        policy = core.Policy(**config["policy"])
        policy.validate()
        identifier(policy.version)
    except (TypeError, KeyError, ValueError):
        raise Closed("invalid_policy") from None
    missing = []
    live = config.get("live", {})
    required = (
        "enabled",
        "outbound_approval_ref",
        "batch_id",
        "source_plan_sha256",
        "contract_version",
        "catalog_sha256",
        "expires_at",
        "provider_cap_confirmed",
        "provider_cap_usd",
        "budget",
    )
    if not isinstance(live, dict):
        raise Closed("invalid_live_config")
    require(not set(live) - set(required), "invalid_live_config_fields")
    if "budget" in live:
        require(
            isinstance(live["budget"], dict)
            and not set(live["budget"])
            - {
                "kind",
                "pool_id",
                "approval_ref",
                "pricing_ref",
                "limit_usd",
                "reserve_per_stage_usd",
                "input_usd_per_million",
                "output_usd_per_million",
            },
            "invalid_budget_fields",
        )
    missing.extend("live." + name for name in required if name not in live)
    if not path or policy.version == OFFLINE_POLICY.version:
        missing.append("approved_research_policy")
    if mode == "live":
        if missing:
            raise Closed("live_configuration_incomplete", missing=missing)
        require(live["enabled"] is True and live["provider_cap_confirmed"] is True, "live_not_authorized")
        require(
            live["batch_id"] == batch_id
            and live["source_plan_sha256"] == PLAN_SHA256
            and live["contract_version"] == core.VERSION
            and live["catalog_sha256"] == core.CATALOG_SHA256,
            "live_scope_mismatch",
        )
        identifier(live["outbound_approval_ref"])
        unexpired(live["expires_at"])
        budget = live["budget"]
        require(isinstance(budget, dict) and budget.get("kind") == "new_dedicated", "dedicated_new_budget_required")
        for name in ("pool_id", "approval_ref", "pricing_ref"):
            identifier(budget.get(name))
        for name in ("limit_usd", "reserve_per_stage_usd", "input_usd_per_million", "output_usd_per_million"):
            number(budget.get(name), positive=name in {"limit_usd", "reserve_per_stage_usd"})
        require(
            number(live["provider_cap_usd"], positive=True) <= number(budget["limit_usd"]),
            "provider_cap_exceeds_budget",
        )
        require(2 * number(budget["reserve_per_stage_usd"]) <= number(budget["limit_usd"]), "budget_insufficient")
    return config, policy, missing


class Gate:
    def __init__(self, root, source_plan, batch, config, config_path, mode):
        self.root, self.source_plan, self.batch = root, source_plan, batch
        self.config, self.config_path, self.mode = config, config_path, mode
        self.ledger = None
        self.reason = None

    def check(self):
        require(not (self.batch / "CANCEL").exists(), "cancelled")
        source_admission(self.root, self.source_plan)
        if self.config_path:
            require(decode(read(self.config_path)) == self.config, "authorization_or_policy_changed")
        if self.mode == "live":
            configuration(self.config_path, "live", self.batch.name.removeprefix("m55-two-stage-"))
        if self.ledger:
            require(self.ledger.committed <= self.ledger.limit, "budget_exhausted")

    def allowed(self):
        try:
            self.check()
            return True
        except Closed as exc:
            self.reason = str(exc)
            return False
        except OSError:
            self.reason = "source_admission_unavailable"
            return False


class OfflineGuard:
    """A process audit hook permits only explicit data/config reads and import files.

    In particular, passing a real keys-file in Mock is rejected before this hook;
    no code path may open arbitrary credentials or reach a network socket.
    """

    def __init__(self, files, code_root):
        self.files = {Path(path).resolve() for path in files}
        self.roots = {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve(), Path(code_root).resolve() / "src"}
        self.active = False
        self.network_denied = 0

    def __enter__(self):
        self.active = True
        sys.addaudithook(self.audit)
        return self

    def __exit__(self, *_):
        self.active = False

    def audit(self, event, args):
        if not self.active:
            return
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen", "os.system"}:
            self.network_denied += 1
            raise Closed("offline_network_forbidden")
        if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        # Pure writes contain only this program's safe records / temporary fake key.
        mode, flags = args[1], args[2]
        if isinstance(flags, int) and flags & os.O_WRONLY:
            return
        if mode in {"w", "wb", "a", "ab"}:
            return
        if path in self.files:
            return
        if path.suffix.lower() in {".py", ".pyc", ".pyd", ".dll", ".so"} and any(
            path.is_relative_to(p) for p in self.roots
        ):
            return
        raise Closed("offline_file_read_forbidden")


class Inputs:
    """Hash and deserialize the same buffers; never copy controlled body stores."""

    def __init__(self, root, plan, gate):
        self.root, self.plan, self.gate = root, plan, gate
        self.connections = []
        self.context_reads = 0

    def __enter__(self):
        self.gate.check()
        for entry in [*self.plan["sources"], *self.plan["input_pins"].values()]:
            self.gate.check()
            path = research_path(self.root, entry["path"])
            with path.open("rb") as stream:
                digest, size = hashlib.sha256(), 0
                while chunk := stream.read(1024 * 1024):
                    unexpired(self.plan["expires_at"])
                    digest.update(chunk)
                    size += len(chunk)
            require(digest.hexdigest() == entry["sha256"] and size == entry["bytes"], "source_snapshot_changed")
        try:
            entry = self.plan["input_pins"]["inputs"]
            self.original = self.open_db(research_path(self.root, entry["path"]), entry["sha256"], entry["bytes"])
            self.outbound = self.open_db(
                self.root / PREP / "outbound.sqlite3",
                self.plan["db_sha256"],
                self.plan["db_bytes"],
            )
            require(
                {r[0] for r in self.outbound.execute("SELECT target_id FROM inputs")}
                == {t["target_id"] for t in self.plan["targets"] if t["eligible"]},
                "prepared_rows_changed",
            )
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_):
        for connection in self.connections:
            connection.close()

    def open_db(self, path, expected, size):
        self.gate.check()
        raw = read(path, 128 * 1024 * 1024, expected)
        require(len(raw) == size, "database_size_changed")
        connection = sqlite3.connect(":memory:")
        self.connections.append(connection)
        connection.deserialize(raw)
        connection.execute("PRAGMA query_only=ON")
        require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "database_invalid")
        return connection

    def state(self, target):
        self.gate.check()
        self.context_reads += 1
        key = target["target_id"]
        raw = self.outbound.execute("SELECT state_json FROM inputs WHERE target_id=?", (key,)).fetchone()
        original = self.original.execute("SELECT payload FROM inputs WHERE input_id=?", (key,)).fetchone()
        require(raw is not None and original is not None, "missing_target")
        require(sha(raw[0].encode()) == target["outbound_state_sha256"], "state_fingerprint_changed")
        return adapt(target, decode(raw[0]), decode(original[0]))


def adapt(target, state, original):
    """Use the exact target-bound pre-answer projection; no annotation is material.

    HelpSteer3 selects response1/response2, WildFB selects messages/1,
    WildFeedback selects a pinned odd utterance after exactly N earlier turns.
    Optional evidence other than prior_context has no proven dedicated source field
    in this snapshot, so it is deliberately absent.
    """
    require(target["eligible"] is True and target["source"] in SOURCES, "target_not_admitted")
    require(isinstance(state, dict) and set(state) == {"context", "answer"}, "invalid_input_fields")
    identity = {k: original.get(k) for k in ("source", "revision", "file_sha256", "row_id", "target_id")}
    original_id = sha(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())
    require(
        original_id == target["target_id"] == original.get("input_id")
        and identity["source"] == target["source"]
        and original.get("mapping_version") == "m55-input-v1"
        and state == {"context": original.get("context"), "answer": original.get("answer")},
        "ambiguous_target_association",
    )
    context, answer = state["context"], state["answer"]
    require(isinstance(context, list) and bool(context), "missing_request")
    require(isinstance(answer, str) and bool(answer.strip()), "missing_answer")
    for message in context:
        require(
            isinstance(message, dict)
            and set(message) == {"role", "content"}
            and message["role"] in {"system", "user", "assistant"}
            and isinstance(message["content"], str)
            and bool(message["content"].strip()),
            "invalid_prior_context",
        )
    require(context[-1]["role"] == "user", "ambiguous_target_association")
    source, pointer = target["source"], identity["target_id"]
    if source == "helpsteer3":
        require(pointer in {"response1", "response2"}, "ambiguous_target_association")
    elif source == "wildfb":
        require(pointer == "messages/1", "ambiguous_target_association")
    else:
        match = re.fullmatch(r"utterance/([0-9]+)", pointer or "")
        require(
            match is not None and int(match[1]) == len(context) and len(context) % 2 == 1,
            "ambiguous_target_association",
        )
        require(
            all(m["role"] == ("user" if i % 2 == 0 else "assistant") for i, m in enumerate(context)),
            "ambiguous_target_association",
        )
    projected = {"request": context[-1]["content"], "answer": answer}
    if len(context) > 1:
        projected["prior_context"] = core._encode(context[:-1]).decode()
    return core._project(projected)


def make_plan(identity, source_plan, config, batch_id, inputs):
    targets = []
    catalog = core.load_catalog()
    questions = core.route_questions(catalog)
    for target in source_plan["targets"]:
        row = {k: target[k] for k in ("target_id", "source", "group_id", "expires_at", "original_state_sha256")}
        row.update(input_sha256=None, route_request_sha256=None, route_request_utf8_bytes=None)
        if not target["eligible"]:
            row.update(disposition="original_exclusion", exclusion_code=target["exclusion_code"])
        else:
            try:
                state = inputs.state(target)
                body = core.request_bytes(
                    {k: v for k, v in state.items() if k not in {"answer", "tool_trace"}}, questions
                )
                row.update(
                    input_sha256=sha(core._encode(state)),
                    route_request_sha256=sha(body),
                    route_request_utf8_bytes=len(body),
                )
                require(len(body) <= core.MAX_REQUEST_BYTES, "route_request_too_large")
                row.update(disposition="runnable", exclusion_code=None)
            except ValueError as exc:
                code = str(exc)
                require(
                    code
                    in {
                        "invalid_input_fields",
                        "ambiguous_target_association",
                        "missing_request",
                        "missing_answer",
                        "invalid_prior_context",
                        "projection_too_large",
                        "route_request_too_large",
                    },
                    "input_validation_failed",
                )
                row.update(disposition="new_exclusion", exclusion_code=code)
        targets.append(row)
    return {
        "schema_version": VERSION,
        "batch_id": batch_id,
        "runtime": identity,
        "source_plan_sha256": PLAN_SHA256,
        "input_pins": source_plan["input_pins"],
        "outbound_sha256": source_plan["db_sha256"],
        "sources": source_plan["sources"],
        "expires_at": source_plan["expires_at"],
        "config": config,
        "mode_ownership": {"mock": "synthetic:" + batch_id, "live": config.get("live", {}).get("budget")},
        "second_stage_size": {"measurement": "unavailable_until_actual_route", "request_utf8_bytes": None},
        "seed": 42,
        "source_batch": "batch-1000-final",
        "targets": targets,
        "quality_status": "NOT_EVALUATED",
        "accuracy": None,
        "f1": None,
    }


def save(path, value, *, new=False):
    raw = encode(value) + b"\n"
    if new:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


@contextmanager
def batch_lock(path):
    """OS-owned lock: process death releases it, without deleting historical files."""
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Closed("batch_already_running") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def register_pool(root, batch, plan, *, create=True):
    """A new directory cannot silently reuse a dedicated approval or budget pool."""
    budget = plan["config"]["live"]["budget"]
    path = root / RESEARCH / "two-stage-budget-pools.sqlite3"
    if not create and not path.exists():
        return
    db = sqlite3.connect(path) if create else sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if create:
            db.execute(
                "CREATE TABLE IF NOT EXISTS pools(pool_id TEXT PRIMARY KEY, approval TEXT UNIQUE, batch TEXT, pin TEXT)"
            )
        binding = {key: plan[key] for key in ("config", "runtime", "source_plan_sha256")}
        row = (budget["pool_id"], budget["approval_ref"], str(batch), sha(encode(binding)))
        existing = db.execute("SELECT * FROM pools WHERE pool_id=? OR approval=?", row[:2]).fetchall()
        require(not existing or existing == [row], "budget_pool_already_bound_create_no_new_allowance")
        require(not existing or (batch / "live.sqlite3").is_file(), "bound_live_ledger_missing_reconcile")
        if not existing and create:
            db.execute("INSERT INTO pools VALUES (?,?,?,?)", row)
            db.commit()
    finally:
        db.close()


class Ledger:
    """Safe append-only events plus transactional current state, one serial writer."""

    def __init__(self, path, plan, mode, *, readonly=False):
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.plan, self.mode = plan, mode
        self.budget = MOCK_BUDGET if mode == "mock" else plan["config"]["live"]["budget"]
        self.limit = number(self.budget["limit_usd"])
        self.reserve = number(self.budget["reserve_per_stage_usd"])
        self.invocation_starts = 0
        identity = encode({"plan_sha256": sha(encode(plan)), "mode": mode}).decode()
        if readonly:
            existing = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
            require(existing is not None and existing[0] == identity, "ledger_identity_changed")
            self.refresh_budget()
            return
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT, result TEXT);
                CREATE TABLE IF NOT EXISTS stages(
                    target TEXT, name TEXT, ref TEXT UNIQUE, status TEXT, held TEXT, estimate TEXT, metadata TEXT,
                    PRIMARY KEY(target,name));
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, event TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            existing = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
            require(existing is None or existing[0] == identity, "ledger_identity_changed")
            if existing is None:
                self.db.execute("INSERT INTO meta VALUES ('identity',?)", (identity,))
                self.db.executemany(
                    "INSERT INTO targets VALUES (?,'pending',NULL,NULL)",
                    [(t["target_id"],) for t in plan["targets"] if t["disposition"] == "runnable"],
                )
        self.refresh_budget()

    def close(self):
        self.db.close()

    def refresh_budget(self):
        amounts = self.db.execute("SELECT held,estimate FROM stages").fetchall()
        self.held = sum((number(r[0]) for r in amounts), Decimal(0))
        self.estimate = sum((number(r[1]) for r in amounts), Decimal(0))

    @property
    def committed(self):
        return self.held + self.estimate

    def event(self, kind, payload):
        self.db.execute("INSERT INTO events(event,payload) VALUES (?,?)", (kind, encode(payload).decode()))

    def recover(self):
        for target in self.db.execute("SELECT id FROM targets WHERE status='running'").fetchall():
            key = target[0]
            sent = self.db.execute(
                "SELECT 1 FROM stages WHERE target=? AND status IN ('started','completed','failed_or_unknown')",
                (key,),
            ).fetchone()
            self.finish(key, "reconcile" if sent else "pending", "interrupted_after_start" if sent else None)

    def pending(self):
        return {row[0] for row in self.db.execute("SELECT id FROM targets WHERE status='pending'")}

    def claim(self, target):
        require(self.committed + 2 * self.reserve <= self.limit, "budget_insufficient")
        key = target["target_id"]
        refs = {stage: f"{self.plan['batch_id']}:{self.mode}:{key}:{stage}" for stage in ("route", "reasons")}
        with self.db:
            require(
                self.db.execute("SELECT status FROM targets WHERE id=?", (key,)).fetchone()[0] == "pending",
                "target_not_pending",
            )
            self.db.execute("UPDATE targets SET status='running',error=NULL WHERE id=?", (key,))
            for stage, ref in refs.items():
                self.db.execute(
                    "INSERT INTO stages VALUES (?,?,?,'reserved',?,'0','{}') "
                    "ON CONFLICT(target,name) DO UPDATE SET status='reserved',held=excluded.held,estimate='0',metadata='{}'",
                    (key, stage, ref, str(self.reserve)),
                )
            self.event(
                "claim",
                {"target_id": key, "source": target["source"], "input_sha256": target["input_sha256"], "refs": refs},
            )
        self.held += 2 * self.reserve
        return refs

    def stage(self, key, event, record):
        name, status = record["stage"], record["status"]
        row = self.db.execute("SELECT * FROM stages WHERE target=? AND name=?", (key, name)).fetchone()
        require(row is not None and row["ref"] == record["budget_reservation_ref"], "reservation_missing")
        held, estimate = self.reserve, Decimal(0)
        if status == "completed":
            usage = record["usage"]
            estimate = (
                Decimal(usage["input_tokens"]) * number(self.budget["input_usd_per_million"])
                + Decimal(usage["output_tokens"]) * number(self.budget["output_usd_per_million"])
            ) / 1000000
            held = Decimal(0)
        with self.db:
            self.db.execute(
                "UPDATE stages SET status=?,held=?,estimate=?,metadata=? WHERE target=? AND name=?",
                (
                    status,
                    str(held),
                    str(estimate),
                    encode(record).decode(),
                    key,
                    name,
                ),
            )
            self.event(event, {"target_id": key, "record": record, "held": str(held), "usage_estimate": str(estimate)})
        self.held += held - number(row["held"])
        self.estimate += estimate - number(row["estimate"])
        if event == "started":
            self.invocation_starts += 1

    def finish(self, key, status, error=None, result=None):
        with self.db:
            unsent = self.db.execute(
                "SELECT name,held FROM stages WHERE target=? AND status IN ('reserved','not_sent')",
                (key,),
            ).fetchall()
            for row in unsent:
                self.db.execute("UPDATE stages SET held='0',status='not_sent' WHERE target=? AND name=?", (key, row[0]))
                self.held -= number(row[1])
            self.db.execute(
                "UPDATE targets SET status=?,error=?,result=? WHERE id=?",
                (
                    status,
                    error,
                    encode(result).decode() if result is not None else None,
                    key,
                ),
            )
            self.event("outcome", {"target_id": key, "status": status, "error_code": error, "result": result})


def synthetic_response(request):
    """Content-independent synthetic answers, never a quality prediction."""
    import httpx

    body = decode(request.content)
    salt = int(sha(request.content)[:8], 16)
    answers = {}
    for name, question in body["questions"].items():
        if name == "task":
            selected = ("code_rewrite", "summarize", "unknown")[salt % 3]
        elif name == "domain":
            selected = ("software", "language", "general")[salt % 3]
        else:
            selected = ("yes", "no", "unknown")[salt % 3]
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "confidence": 1.0,
            "probabilities": {key: float(key == selected) for key in question["criteria"]},
        }
    return httpx.Response(
        200,
        json={
            "model": MODEL,
            "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    )


async def execute(plan, source_plan, ledger, gate, inputs, keys_file, policy, transport):
    pending = ledger.pending()
    originals = {t["target_id"]: t for t in source_plan["targets"]}
    calls = 0
    for target in plan["targets"]:
        key = target["target_id"]
        if key not in pending:
            continue
        gate.check()
        refs = ledger.claim(target)

        def state_factory(key=key, target=target):
            state = inputs.state(originals[key])
            require(sha(core._encode(state)) == target["input_sha256"], "adapted_input_changed")
            return state

        def stage_callback(event, record, key=key):
            nonlocal calls
            if event == "started":
                gate.check()
            ledger.stage(key, event, record)
            if event == "started":
                calls += 1
                gate.check()

        try:
            result = await core.classify(
                state_factory,
                keys_file=keys_file,
                policy=policy,
                enabled=True,
                outbound_approval_ref=(plan["config"].get("live", {}).get("outbound_approval_ref", "synthetic-only")),
                budget_reservations=refs,
                admission_check=gate.allowed,
                transport=transport,
                stage_callback=stage_callback,
            )
            gate.check()
            ledger.finish(key, "success", result=result)
        except core.StageError as exc:
            code = str(exc)
            if code == "admission_revoked":
                code = gate.reason or code
            ledger.finish(key, "error", code)
            if str(exc) == "admission_revoked":
                raise Closed(code) from None
        except (NotFoundError, Closed) as exc:
            sent = ledger.db.execute(
                "SELECT 1 FROM stages WHERE target=? AND status IN ('started','completed','failed_or_unknown')",
                (key,),
            ).fetchone()
            code = str(exc) if isinstance(exc, Closed) else gate.reason or "admission_denied"
            ledger.finish(key, "error" if sent else "pending", code)
            raise Closed(code) from None
        except ValueError:
            ledger.finish(key, "error", "local_validation_failed")
    return calls


def summary(plan, ledger=None):
    dispositions = Counter(t["disposition"] for t in plan["targets"])
    statuses, outcomes, requests, unknown = Counter(), Counter(), Counter(), Decimal(0)
    usage = {name: {"input_tokens": 0, "output_tokens": 0} for name in ("route", "reasons")}
    if ledger:
        for row in ledger.db.execute("SELECT status,result FROM targets"):
            statuses["reconcile" if row[0] == "running" else row[0]] += 1
            if row[0] == "success":
                outcomes[decode(row[1])["outcome"]] += 1
        for row in ledger.db.execute("SELECT * FROM stages"):
            if row["status"] in {"started", "completed", "failed_or_unknown"}:
                requests[row["name"]] += 1
            if row["status"] in {"started", "failed_or_unknown"}:
                unknown += number(row["held"])
            record = decode(row["metadata"])
            if row["status"] == "completed":
                for key, value in record["usage"].items():
                    usage[row["name"]][key] += value
    else:
        statuses["pending"] = dispositions["runnable"]
    require(sum(statuses.values()) == dispositions["runnable"], "summary_count_mismatch")
    require(sum(outcomes.values()) == statuses["success"], "outcome_count_mismatch")
    return {
        "schema_version": VERSION,
        "batch_id": plan["batch_id"],
        "mode": ledger.mode if ledger else "preview",
        "synthetic": ledger.mode == "mock" if ledger else False,
        "quality_status": "NOT_EVALUATED",
        "accuracy": None,
        "f1": None,
        "actual_charge_usd": None,
        "original_targets": len(plan["targets"]),
        "original_excluded": dispositions["original_exclusion"],
        "new_excluded": dispositions["new_exclusion"],
        "runnable": dispositions["runnable"],
        "exclusions": {
            kind: dict(Counter(t["exclusion_code"] for t in plan["targets"] if t["disposition"] == kind))
            for kind in ("original_exclusion", "new_exclusion")
        },
        "sources": {
            source: dict(Counter(t["disposition"] for t in plan["targets"] if t["source"] == source))
            for source in sorted(SOURCES)
        },
        "technical_success": statuses["success"],
        "technical_error": statuses["error"],
        "reconcile": statuses["reconcile"],
        "not_executed": statuses["pending"],
        "success_outcomes": {key: outcomes[key] for key in ("suggested", "unknown", "no_match")},
        "stage_requests_started": {name: requests[name] for name in ("route", "reasons")},
        "known_usage": usage,
        "cost_unit": "synthetic_units" if ledger and ledger.mode == "mock" else "USD_estimate_not_bill",
        "known_usage_estimate": str(ledger.estimate) if ledger else None,
        "unknown_cost_reservations": str(unknown),
        "held_reservations": str(ledger.held) if ledger else "0",
        "maximum_planned_stage_requests": 2 * dispositions["runnable"],
        "all_success": statuses["success"] == dispositions["runnable"] and statuses["success"] > 0,
        "no_automatic_work_remaining": statuses["pending"] == 0,
        "runtime": plan["runtime"],
    }


def export(batch, plan, ledger):
    """Rebuild projections from the committed journal, including interrupted targets."""
    target_rows = {row["id"]: row for row in ledger.db.execute("SELECT * FROM targets")}
    stages = {}
    for row in ledger.db.execute("SELECT * FROM stages ORDER BY CASE name WHEN 'route' THEN 0 ELSE 1 END"):
        stages.setdefault(row["target"], []).append(
            decode(row["metadata"])
            | {
                "stage": row["name"],
                "status": row["status"],
                "budget_reservation_ref": row["ref"],
                "held_reservation": row["held"],
                "known_usage_estimate": row["estimate"],
            }
        )
    path = batch / f"{ledger.mode}.results.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("wb") as stream:
        for target in plan["targets"]:
            row = target_rows.get(target["target_id"])
            result = decode(row["result"]) if row and row["result"] else None
            target_stages = stages.get(target["target_id"], [])
            completed_route = next(
                (stage for stage in target_stages if stage["stage"] == "route" and stage["status"] == "completed"), None
            )
            routes = (
                core.select_routes(completed_route["answers"], core.Policy(**plan["config"]["policy"]))
                if completed_route
                else None
            )
            route_scope = (
                "not_evaluated"
                if routes is None
                else "partial"
                if any(route["status"] == "unresolved" for route in routes.values())
                else "selected_task_and_domain"
            )
            stream.write(
                encode(
                    {
                        "schema_version": VERSION,
                        "batch_id": plan["batch_id"],
                        "mode": ledger.mode,
                        "synthetic": ledger.mode == "mock",
                        **target,
                        "technical_status": ("reconcile" if row["status"] == "running" else row["status"])
                        if row
                        else "excluded",
                        "error_code": row["error"] if row else None,
                        "result": result,
                        "contract_version": core.VERSION,
                        "catalog_version": plan["runtime"]["catalog_version"],
                        "catalog_sha256": plan["runtime"]["catalog_sha256"],
                        "policy": plan["config"]["policy"],
                        "stages": target_stages,
                        "routes": routes,
                        "route_scope": route_scope,
                        "primary_reason": None,
                        "user_confirmed": False,
                        "attribution_source": "model_inferred_unconfirmed",
                        "quality_status": "NOT_EVALUATED",
                    }
                )
                + b"\n"
            )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    value = summary(plan, ledger)
    save(batch / f"{ledger.mode}.summary.json", value)
    return value


def offline_files(root, project_root, source_plan, batch, config_path, key_path):
    return [
        *(project_root / name for name in CODE_FILES),
        root / PREP / "plan.json",
        root / PREP / "outbound.sqlite3",
        *(
            research_path(root, entry["path"])
            for entry in [*source_plan["sources"], *source_plan["input_pins"].values()]
        ),
        batch / "plan.json",
        batch / ".run.lock",
        batch / "mock.sqlite3",
        batch / "mock.sqlite3-journal",
        *([config_path] if config_path else []),
        key_path,
    ]


def run(args, project_root):
    identity = runtime_identity(project_root)
    root = Path(args.data_root or project_root).resolve()
    identifier(args.batch_id)
    batch = root / RESEARCH / ("m55-two-stage-" + args.batch_id)
    require(args.mode == "live" or args.keys_file is None, "keys_file_only_permitted_in_live")
    config_path = research_path(root, args.config_file) if args.config_file else None
    if args.mode == "report":
        require(batch.is_dir(), "batch_missing")
        with batch_lock(batch / ".run.lock"):
            plan = decode(read(batch / "plan.json"))
            require(plan["runtime"] == identity, "batch_fingerprint_changed_create_new_batch")
            path = batch / f"{args.report_mode}.sqlite3"
            require(path.is_file(), "mode_has_no_ledger")
            ledger = Ledger(path, plan, args.report_mode, readonly=True)
            try:
                return export(batch, plan, ledger)
            finally:
                ledger.close()
    config, policy, missing = configuration(config_path, args.mode, args.batch_id)
    if args.mode == "live":
        require(args.keys_file and Path(args.keys_file).is_absolute(), "explicit_absolute_keys_file_required")
    source_plan = metadata(root)
    gate = Gate(root, source_plan, batch, config, config_path, args.mode)
    gate.check()
    batch.mkdir(parents=True, exist_ok=True)
    with (
        batch_lock(batch / ".run.lock"),
        batch_lock(root / RESEARCH / ".two-stage-budget.lock") if args.mode == "live" else nullcontext(),
    ):
        if args.mode == "live":
            register_pool(
                root,
                batch,
                {
                    "config": config,
                    "runtime": identity,
                    "source_plan_sha256": PLAN_SHA256,
                },
                create=False,
            )
        # Fake key exists only for this invocation; the real loader is exercised by Mock.
        with tempfile.TemporaryDirectory(prefix="whynote-two-stage-") as temporary:
            key_path = Path(temporary) / "synthetic-keys.json"
            if args.mode != "live":
                key_path.write_text('{"typesafe":"synthetic-key-only"}', encoding="utf-8")
            guard = OfflineGuard(
                offline_files(root, project_root, source_plan, batch, config_path, key_path), project_root
            )
            if args.mode == "live":
                guard.active = False
            # Windows asyncio creates a loopback self-pipe during loop construction.
            # Construct it before installing the deny-network hook; all task I/O stays guarded.
            with (
                asyncio.Runner() if args.mode == "mock" else nullcontext() as runner,
                guard if args.mode != "live" else nullcontext(),
            ):
                plan_path = batch / "plan.json"
                if plan_path.exists():
                    plan = decode(read(plan_path))
                    require(
                        plan["runtime"] == identity
                        and plan["config"] == config
                        and plan["batch_id"] == args.batch_id
                        and plan["source_plan_sha256"] == PLAN_SHA256
                        and plan["schema_version"] == VERSION,
                        "batch_fingerprint_changed_create_new_batch",
                    )
                else:
                    require(
                        not (batch / "mock.sqlite3").exists() and not (batch / "live.sqlite3").exists(),
                        "frozen_plan_missing_reconcile_do_not_reinitialize",
                    )
                    with Inputs(root, source_plan, gate) as inputs:
                        plan = make_plan(identity, source_plan, config, args.batch_id, inputs)
                    save(plan_path, plan, new=True)
                if args.mode == "preview":
                    result = summary(plan)
                    result["live_missing"] = missing
                    result["estimated_maximum_reservation_usd"] = (
                        str(2 * result["runnable"] * number(config["live"]["budget"]["reserve_per_stage_usd"]))
                        if not missing
                        else None
                    )
                    save(batch / "preview.summary.json", result)
                    return result
                if args.mode == "live":
                    register_pool(root, batch, plan)
                ledger = Ledger(batch / f"{args.mode}.sqlite3", plan, args.mode)
                gate.ledger = ledger
                stopped = None
                reads = 0
                try:
                    ledger.recover()
                    if ledger.pending():
                        gate.check()
                        require(ledger.committed + 2 * ledger.reserve <= ledger.limit, "budget_insufficient")
                        import httpx

                        transport = httpx.MockTransport(synthetic_response) if args.mode == "mock" else None
                        with Inputs(root, source_plan, gate) as inputs:
                            try:
                                (runner.run if runner else asyncio.run)(
                                    execute(
                                        plan,
                                        source_plan,
                                        ledger,
                                        gate,
                                        inputs,
                                        Path(args.keys_file) if args.mode == "live" else key_path,
                                        policy,
                                        transport,
                                    )
                                )
                            finally:
                                reads = inputs.context_reads
                except Closed as exc:
                    stopped = str(exc)
                finally:
                    result = export(batch, plan, ledger)
                    ledger.close()
                return result | {
                    "stopped": stopped,
                    "invocation_stage_starts": ledger.invocation_starts,
                    "invocation_context_reads": reads,
                }


def main(argv=None, *, project_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preview", "mock", "live", "report"), default="preview")
    parser.add_argument("--data-root")
    parser.add_argument("--batch-id", default="offline-v1")
    parser.add_argument("--config-file")
    parser.add_argument("--keys-file")
    parser.add_argument("--report-mode", choices=("mock", "live"), default="mock")
    args = parser.parse_args(argv)
    try:
        result = run(args, Path(project_root or Path(__file__).resolve().parents[2]))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 2 if result.get("stopped") else 0
    except (Closed, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        # Exception strings can contain keys, source text, SQL or provider bodies.
        exc = sys.exception()
        code = str(exc) if type(exc) is Closed else "local_preflight_or_storage_failed"
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "code": code,
                    "missing_configuration": exc.missing if type(exc) is Closed else None,
                    "live_api_calls_in_offline_modes": 0,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
