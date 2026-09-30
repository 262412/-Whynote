"""Source admission and a disk-backed, immutable plan for unlabelled exploration."""

import json
import os
import re
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .explore_inputs import SOURCES, VERSION, WildFeedbackMapper, contained, file_hash, map_record, source_rows
from .replay_laya import ReplayError, require
from .source_mapping import digest

SCHEMA = "m55-explore-v1"
PII = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b|-----BEGIN .*PRIVATE KEY-----")


def now():
    return datetime.now(UTC).isoformat()


def save_json(path, value, *, exclusive=False):
    with Path(path).open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def admission(manifest):
    require(isinstance(manifest, dict) and manifest.get("schema_version") == SCHEMA, "invalid_source_manifest")
    root = Path(manifest["research_root"]).resolve(strict=True)
    require(root.is_dir() and root.is_relative_to(Path(manifest["project_root"]).resolve()), "invalid_research_root")
    sources = manifest.get("sources")
    require(isinstance(sources, list) and sources, "missing_sources")
    require(len({s["source"] for s in sources}) == len(sources), "duplicate_source")
    accepted, holds = [], []
    for item in sources:
        name = item.get("source")
        require(name in SOURCES, "unknown_source")
        if item.get("status") != "ADMITTED_FOR_EXPLORATION":
            holds.append({"source": name, "status": "HOLD", "reason": "source_not_admitted"})
            continue
        try:
            repo, revision, filename, size, sha = SOURCES[name]
            require(
                (item.get("repo"), item.get("revision"), item.get("file"), item.get("bytes"), item.get("sha256"))
                == (repo, revision, filename, size, sha),
                "source_identity_changed",
            )
            require(
                item.get("purpose") == "local_unlabelled_exploration"
                and item.get("approval")
                and item.get("access") == ["project_owner", "local_codex"]
                and item.get("backup") is False,
                "source_usage_missing",
            )
            require(
                all(type(item.get(k)) is int and item[k] > 0 for k in ("max_source_records", "max_targets"))
                and all(type(item.get(k, False)) is bool for k in ("allow_all_scan", "allow_all_targets")),
                "source_scope_invalid",
            )
            expiry = datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
            require(expiry.tzinfo is not None and expiry > datetime.now(UTC), "source_retention_expired")
            path = contained(item["path"], root)
            require(path.stat().st_size == size and file_hash(path) == sha, "source_file_changed")
            accepted.append(dict(item, path=str(path)))
        except ReplayError as exc:
            holds.append({"source": name, "status": "HOLD", "reason": str(exc)})
        except (OSError, ValueError, KeyError, TypeError):
            holds.append({"source": name, "status": "HOLD", "reason": "source_record_invalid"})
    require(accepted, "no_admitted_sources")
    return root, accepted, holds


def prepare(manifest, output, *, records=100, targets=None, schemes=("C",), seed=42, holdout_percent=0):
    root, sources, holds = admission(manifest)
    require(records is None or type(records) is int and records > 0, "invalid_record_limit")
    require(targets is None or type(targets) is int and targets > 0, "invalid_target_limit")
    require(schemes and len(set(schemes)) == len(schemes) and set(schemes) <= {"A", "B", "C"}, "invalid_schemes")
    require(type(seed) is int and type(holdout_percent) is int and 0 <= holdout_percent < 100, "invalid_holdout")
    require(all(records is not None or s.get("allow_all_scan") is True for s in sources), "all_scan_not_admitted")
    require(all(records is None or records <= s["max_source_records"] for s in sources), "record_scope_exceeded")
    require(all(targets is None or targets <= s["max_targets"] for s in sources), "target_scope_exceeded")
    require(
        all(targets is not None and targets <= s["max_targets"] for s in sources)
        or all(records is not None and records <= 100 for s in sources)
        or all(targets is None and records is None and s.get("allow_all_targets") is True for s in sources),
        "target_scope_exceeded",
    )
    output = contained(output, root)
    # Source remains compressed; input/reference index may expand substantially.
    planned = sum(s["bytes"] for s in sources)
    reserve = max(512 * 1024**2, min(planned * 4, (records or (targets or 1000) * 10) * 1048576))
    require(shutil.disk_usage(root).free > reserve, "insufficient_research_disk")
    output.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": SCHEMA,
        "mode": "explore",
        "run_id": uuid.uuid4().hex,
        "created_at": now(),
        "research_root": str(root),
        "sources": sources,
        "holds": holds,
        "mapping_version": VERSION,
        "records_per_source": records,
        "targets_per_source": targets,
        "schemes": list(schemes),
        "seed": seed,
        "holdout_percent": holdout_percent,
        "disk_reserve_bytes": reserve,
        "quality": "NOT_EVALUATED",
        "language_detection": "character_heuristic_v1",
    }
    db = sqlite3.connect(output / "inputs.sqlite3")
    db.executescript("""
        CREATE TABLE inputs (ordinal INTEGER PRIMARY KEY, input_id TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
        group_id TEXT NOT NULL, context_hash TEXT NOT NULL, language TEXT NOT NULL, length TEXT NOT NULL,
        excluded TEXT, payload TEXT NOT NULL);
        CREATE TABLE source_records (source TEXT, row_id INTEGER, outcome TEXT, errors TEXT,
        PRIMARY KEY(source,row_id));
        CREATE INDEX groups_idx ON inputs(group_id);
    """)
    counts = {}
    try:
        for batch in sources:
            source = batch["source"]
            record_limit = records if records is not None else batch["max_source_records"]
            target_limit = (
                targets
                if targets is not None
                else None
                if records is None and batch.get("allow_all_targets") is True
                else batch["max_targets"]
            )
            count, target_count, mapper = Counter(), 0, WildFeedbackMapper()
            previous = None
            iterator = iter(source_rows(batch["path"], source))
            row_id = -1
            while row_id + 1 < record_limit:
                if target_limit is not None and target_count >= target_limit:
                    break
                try:
                    row_id, row, error = next(iterator)
                except StopIteration:
                    break
                except ReplayError as exc:
                    count["scanned"] += 1
                    count["parse_failed"] += 1
                    db.execute(
                        "INSERT INTO source_records VALUES (?,?,?,?)",
                        (source, row_id + 1, "parse_failed", json.dumps([{"error": str(exc)}])),
                    )
                    plan["holds"].append({"source": source, "status": "PARTIAL", "reason": str(exc)})
                    break
                count["scanned"] += 1
                if error:
                    mapped, errors = [], [{"error": error}]
                    outcome = "parse_failed"
                    if source == "wildfeedback":
                        mapper = WildFeedbackMapper()
                else:
                    mapped, errors = (
                        mapper.map(batch, row_id, row)
                        if source == "wildfeedback"
                        else map_record(source, batch, row_id, row)
                    )
                    outcome = "retained" if mapped else "mapping_failed" if errors else "no_target"
                    # Link the following original user utterance only inside the verified same segment.
                    if (
                        source == "wildfeedback"
                        and previous
                        and not errors
                        and row["Role"] == "User"
                        and row["UtterranceId"]
                    ):
                        payload = json.loads(
                            db.execute("SELECT payload FROM inputs WHERE input_id=?", (previous,)).fetchone()[0]
                        )
                        payload["reference"].update(feedback=[row["Content"]], feedback_origin="original_user")
                        db.execute(
                            "UPDATE inputs SET payload=? WHERE input_id=?",
                            (json.dumps(payload, ensure_ascii=False), previous),
                        )
                previous = None
                db.execute("INSERT INTO source_records VALUES (?,?,?,?)", (source, row_id, outcome, json.dumps(errors)))
                count[outcome] += 1
                count["rejected_targets"] += len(errors)
                for item in mapped:
                    if target_limit is not None and target_count >= target_limit:
                        count["targets_not_selected"] += 1
                        continue
                    context_hash = item["conversation_group_id"]
                    group = item.get("source_conversation_id", context_hash)
                    excluded = "sensitive_pattern" if PII.search(item["state"]) else None
                    # Root request groups are conservatively kept together for optional holdout.
                    split_group = digest(item["context"][0])
                    if int(digest([seed, split_group])[:8], 16) % 100 < holdout_percent:
                        excluded = "reserved_group"
                    db.execute(
                        "INSERT INTO inputs(input_id,source,group_id,context_hash,language,length,excluded,payload) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            item["input_id"],
                            source,
                            group,
                            context_hash,
                            item["language"],
                            item["length_bucket"],
                            excluded,
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
                    previous = item["input_id"]
                    target_count += 1
                    count["targets"] += 1
                    if excluded:
                        count[excluded] += 1
                if count["scanned"] % 100 == 0:
                    db.commit()
            iterator.close()
            counts[source] = dict(count)
            db.commit()
        plan["source_counts"] = counts
        plan["targets"] = db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0]
        plan["conversation_groups"] = db.execute("SELECT COUNT(DISTINCT group_id) FROM inputs").fetchone()[0]
        plan["shared_context_groups"] = db.execute(
            "SELECT COUNT(*) FROM (SELECT context_hash FROM inputs GROUP BY context_hash HAVING COUNT(DISTINCT source)>1)"
        ).fetchone()[0]
        plan["unresolved_upstream_near_duplicates"] = True
        plan["planned_slots"] = plan["targets"] * len(schemes)
    finally:
        db.close()
    plan["inputs_sha256"] = file_hash(output / "inputs.sqlite3")
    save_json(output / "manifest.json", plan, exclusive=True)
    return plan


def read_plan(output):
    output = Path(output).resolve(strict=True)
    plan = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(plan.get("schema_version") == SCHEMA and plan.get("mode") == "explore", "invalid_explore_plan")
    contained(output, plan["research_root"])
    require(file_hash(output / "inputs.sqlite3") == plan["inputs_sha256"], "prepared_inputs_changed")
    return plan
