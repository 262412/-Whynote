"""Pinned HelpSteer3 projection and actual tokenizer measurements; never uses labels."""

import gzip
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .laya_local import QUESTIONS
from .laya_manifest import SHA256
from .replay import DECISIONS, REASONS, ROUTE_B, ROUTE_C, state_text
from .replay_laya import require
from .source_mapping import REVISIONS, MappingError, digest, messages


def group_hash(context):
    normalized = [
        {"role": m["role"], "content": " ".join(unicodedata.normalize("NFKC", m["content"]).split())} for m in context
    ]
    return digest(normalized)


def project_helpsteer(row, target):
    require(
        isinstance(row, dict)
        and set(row) == {"context", "response1", "response2", "feedback1", "feedback2", "domain", "language"},
        "source_schema_mismatch",
    )
    require(target in ("response1", "response2"), "invalid_target")
    try:
        context = messages(row["context"])
    except MappingError:
        require(False, "source_context_invalid")
    require(context[-1]["role"] == "user", "source_context_not_final_user")
    answer = row[target]
    require(isinstance(answer, str) and bool(answer.strip()), "source_answer_invalid")
    feedback = row["feedback" + target[-1]]
    require(
        isinstance(feedback, list) and feedback and all(isinstance(v, str) and v.strip() for v in feedback),
        "source_feedback_invalid",
    )
    state = state_text({"context": context, "answer": answer})
    kinds = ["request", "answer"]
    if re.search(r"(?m)^```[^\n]*\n[\s\S]+?\n```[ \t]*$", context[-1]["content"]):
        kinds.append("original_code")
    return {
        "state": state,
        "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "input_utf8_bytes": len(state.encode()),
        "evidence_kinds": kinds,
        "context_sha256": digest(context),
        "normalized_group_sha256": group_hash(context),
        "target_sha256": digest(answer),
        "feedback_sha256": digest(feedback),
        "feedback_origin": "evaluator",
    }


def selected_rows(path, row_ids, *, maximum_row):
    require(
        isinstance(row_ids, set) and row_ids and all(type(n) is int and 0 <= n <= maximum_row for n in row_ids),
        "invalid_selected_rows",
    )
    found = {}
    try:
        with gzip.open(path, "rb") as stream:
            for index in range(max(row_ids) + 1):
                line = stream.readline(1048577)
                require(line and len(line) <= 1048576, "source_line_unavailable_or_oversized")
                if index in row_ids:
                    found[index] = json.loads(line)
    except (OSError, UnicodeError, json.JSONDecodeError):
        require(False, "source_file_invalid")
    require(set(found) == row_ids, "missing_selected_row")
    return found


def input_identity(batch, row_id, target):
    return digest(
        {
            "source": batch["source"],
            "revision": batch["revision"],
            "file_sha256": batch["file_sha256"],
            "row_id": row_id,
            "target_key": target,
        }
    )


def load_projections(manifest, exposures):
    """Admission and file checks must run first. Exposures are pinned in each admission."""
    samples = manifest["samples"]
    projections = {}
    held_groups = set()
    for batch in manifest["admissions"]:
        require(
            batch["source"] == "helpsteer3" and batch["revision"] == REVISIONS["helpsteer3"],
            "source_adapter_not_enabled",
        )
        exposure = exposures.get(batch["batch_id"])
        require(
            isinstance(exposure, dict)
            and set(exposure) == {"schema_version", "groups"}
            and exposure["schema_version"] == "m54a-exposed-groups-v1"
            and isinstance(exposure["groups"], list),
            "missing_exposure_ledger",
        )
        require(digest(exposure) == batch["exclusions_sha256"], "exposure_ledger_changed")
        held_groups.update(exposure["groups"])
    validation_groups, exploration_groups = set(), set()
    for batch in manifest["admissions"]:
        subset = [s for s in samples if s["batch_id"] == batch["batch_id"]]
        require(subset, "unused_admission")
        rows = selected_rows(batch["file_path"], {s["row_id"] for s in subset}, maximum_row=batch["row_end"])
        for item in subset:
            projection = project_helpsteer(rows[item["row_id"]], item["target_key"])
            require(
                item["input_id"] == input_identity(batch, item["row_id"], item["target_key"]), "source_identity_changed"
            )
            require(
                projection["state_sha256"] == item["state_sha256"]
                and projection["input_utf8_bytes"] == item["input_utf8_bytes"],
                "prediction_material_changed",
            )
            group = projection["normalized_group_sha256"]
            require(item["group_ids"]["conversation"] == group, "conversation_group_changed")
            target_groups = validation_groups if item["partition"] == "validation" else exploration_groups
            require(group not in target_groups, "duplicate_normalized_context")
            target_groups.add(group)
            if item["partition"] == "validation":
                require(
                    group not in held_groups and projection["context_sha256"] not in held_groups,
                    "validation_already_exposed",
                )
            projections[item["input_id"]] = projection
    require(not validation_groups & exploration_groups, "normalized_group_leakage")
    return projections


def load_tokenizer(model_dir):
    from importlib.metadata import version

    from .laya_local import SDK_VERSIONS

    require(all(version(name) == expected for name, expected in SDK_VERSIONS.items()), "tokenizer_runtime_mismatch")
    # The supervisor canonicalizes this path before granting access. GetFinalPathName
    # on a directory requests access that the restricted Windows token cannot obtain.
    model_dir = Path(model_dir)
    require(model_dir.is_absolute() and model_dir.is_dir(), "invalid_model_path")
    for name in ("tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json"):
        require(hashlib.sha256((model_dir / name).read_bytes()).hexdigest() == SHA256[name], "tokenizer_hash_mismatch")
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(str(model_dir / "tokenizer"), local_files_only=True, trust_remote_code=False)


def measure_budget(tokenizer, state):
    from laya.agent import Agent
    from laya.common import encode_text, render_options

    require(isinstance(state, str) and state.strip(), "invalid_prediction_state")

    def count(text):
        return len(encode_text(tokenizer, text, add_special_tokens=False)["input_ids"])

    size = count(state)
    questions = {"A/" + k: v for k, v in QUESTIONS.items()}
    questions.update({"B/route": ROUTE_B["route"], "C/route": ROUTE_C["route"]})
    questions.update(
        {r.reason_id: {"type": "choice", "instructions": r.criteria, "criteria": DECISIONS} for r in REASONS}
    )
    heads, option_max, option_total = {}, 0, 0
    for key, question in questions.items():
        internal = Agent._to_internal(question)
        sizes = [count(" " + option) for option in render_options(internal)]
        total = sum(n + 1 for n in sizes)
        heads[key] = {
            "instruction_tokens": count(f"{internal['t']} question: {internal['ins']}"),
            "option_tokens": sizes,
            "option_total_tokens": total,
        }
        option_max, option_total = max(option_max, max(sizes)), max(option_total, total)
    head = max(v["instruction_tokens"] + v["option_total_tokens"] for v in heads.values())
    evidence = {
        "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "state_tokens": size,
        "heads": heads,
        "tokenizer_sha256": {k: v for k, v in SHA256.items() if k.startswith("tokenizer/")},
    }
    return {
        "input_utf8_bytes": len(state.encode()),
        "state_tokens": size,
        "head_tokens": head,
        "total_tokens": size + head + 4,
        "max_option_tokens": option_max,
        "option_total_tokens": option_total,
        "reserved_token": any(t in state for t in tokenizer.all_special_tokens),
        "budget_evidence_sha256": digest(evidence),
        "evidence": evidence,
    }
