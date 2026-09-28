"""Versioned offline task reasons; no model, host events or user confirmations."""

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

PACKAGE_VERSION = "m5-task-reasons-v1"
CRITERIA_VERSION = "m5-task-criteria-v1"
TEMPLATE_VERSION = "m5-task-templates-v1"
CATALOG_SHA256 = "ef5743a48090692e424335b4d11099f3ac9be6316c2b1c646a56f2dab65cf814"
TASK_TYPES = ("code_rewrite", "general", "other", "mixed", "unknown")
EVIDENCE_KINDS = ("request", "answer", "original_code", "reference")


class ReasonContractError(ValueError):
    """A constant error code, without caller content."""


def _require(condition, code):
    if not condition:
        raise ReasonContractError(code)


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Reason:
    reason_id: str
    reason_family: str
    task_types: tuple[str, ...]
    domains: tuple[str, ...]
    label: str
    definition: str
    example: str
    counterexample: str
    criteria: str
    required_evidence: tuple[str, ...]
    template: str
    legacy_reason_code: str | None


def load_reasons():
    """Load only the packaged, pinned catalogue, including from an installed wheel."""
    try:
        data = json.loads(files("whynote").joinpath("task_reasons.json").read_text(encoding="utf-8"))
        _require(_digest(data) == CATALOG_SHA256, "catalog_content_mismatch")
        _require(data["package_version"] == PACKAGE_VERSION, "package_version_mismatch")
        _require(data["criteria_version"] == CRITERIA_VERSION, "criteria_version_mismatch")
        _require(data["template_version"] == TEMPLATE_VERSION, "template_version_mismatch")
        return tuple(
            Reason(**(r | {key: tuple(r[key]) for key in ("task_types", "domains", "required_evidence")}))
            for r in data["reasons"]
        )
    except ReasonContractError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ReasonContractError("invalid_catalog") from None


def _values(value, allowed, *, nonempty=False):
    _require(isinstance(value, (list, tuple)) and len(value) <= len(allowed), "invalid_arguments")
    _require(all(isinstance(v, str) and v in allowed for v in value), "invalid_arguments")
    _require(bool(value) or not nonempty, "missing_task")
    return tuple(v for v in allowed if v in value)


@dataclass(frozen=True)
class CandidateSet:
    package_version: str
    criteria_version: str
    template_version: str
    catalog_sha256: str
    task_types: tuple[str, ...]
    fallback_tasks: tuple[str, ...]
    evidence_kinds: tuple[str, ...]
    routed_reason_ids: tuple[str, ...]
    reason_ids: tuple[str, ...]
    unavailable: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def candidate_set_id(self):
        return _digest(asdict(self))


def prepare_candidates(task_types, evidence_kinds, fallback_tasks=(), *, package_version=PACKAGE_VERSION):
    _require(package_version == PACKAGE_VERSION, "package_version_mismatch")
    tasks = _values(task_types, TASK_TYPES, nonempty=True)
    fallbacks = _values(fallback_tasks, TASK_TYPES)
    evidence = _values(evidence_kinds, EVIDENCE_KINDS)
    requested = set(tasks) | set(fallbacks)
    include_code = bool(requested & {"code_rewrite", "mixed", "unknown", "other"})
    routed, selectable, unavailable = [], [], []
    for reason in load_reasons():
        if "general" not in reason.task_types and not include_code:
            continue
        routed.append(reason.reason_id)
        missing = tuple(kind for kind in reason.required_evidence if kind not in evidence)
        if missing:
            unavailable.append((reason.reason_id, missing))
        else:
            selectable.append(reason.reason_id)
    return CandidateSet(
        PACKAGE_VERSION,
        CRITERIA_VERSION,
        TEMPLATE_VERSION,
        CATALOG_SHA256,
        tasks,
        fallbacks,
        evidence,
        tuple(routed),
        tuple(selectable),
        tuple(unavailable),
    )


def _validate_candidates(candidates):
    _require(type(candidates) is CandidateSet, "invalid_candidate_set")
    expected = prepare_candidates(
        candidates.task_types,
        candidates.evidence_kinds,
        candidates.fallback_tasks,
        package_version=candidates.package_version,
    )
    _require(candidates == expected, "candidate_set_mismatch")


def describe_candidates(candidates):
    _validate_candidates(candidates)
    return asdict(candidates) | {
        "candidate_set_id": candidates.candidate_set_id,
        "scope": "offline_only",
        "reasons": [asdict(r) for r in load_reasons() if r.reason_id in candidates.reason_ids],
    }


def validate_selection(selection, candidates):
    """Validate a supplied offline result; validation is not evidence of correctness."""
    _validate_candidates(candidates)
    _require(
        isinstance(selection, dict) and set(selection) == {"candidate_set_id", "outcome", "reason_ids"},
        "invalid_selection",
    )
    _require(selection["candidate_set_id"] == candidates.candidate_set_id, "candidate_set_mismatch")
    outcome, reason_ids = selection["outcome"], selection["reason_ids"]
    _require(outcome in ("suggested", "unknown", "no_match"), "invalid_outcome")
    _require(isinstance(reason_ids, list) and all(isinstance(r, str) for r in reason_ids), "invalid_reason_ids")
    if outcome == "suggested":
        _require(1 <= len(reason_ids) <= 3 and len(set(reason_ids)) == len(reason_ids), "invalid_reason_count")
        _require(set(reason_ids) <= set(candidates.reason_ids), "reason_not_selectable")
    else:
        _require(not reason_ids, "abstention_with_reasons")
        if outcome == "no_match":
            _require({"request", "answer"} <= set(candidates.evidence_kinds), "insufficient_evidence")
    return {
        "candidate_set_id": candidates.candidate_set_id,
        "package_version": candidates.package_version,
        "criteria_version": candidates.criteria_version,
        "template_version": candidates.template_version,
        "catalog_sha256": candidates.catalog_sha256,
        "outcome": outcome,
        "reason_ids": list(reason_ids),
        "selection_origin": "caller_supplied",
        "confirmation_status": "unconfirmed",
        "scope": "offline_only",
    }


def _selection_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "invalid_selection_file")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", choices=TASK_TYPES, required=True)
    parser.add_argument("--fallback-task", action="append", choices=TASK_TYPES, default=[])
    parser.add_argument("--evidence", action="append", choices=EVIDENCE_KINDS, default=[])
    parser.add_argument("--package-version", default=PACKAGE_VERSION)
    parser.add_argument("--selection-file", type=Path)
    args = parser.parse_args(argv)
    try:
        candidates = prepare_candidates(
            args.task,
            args.evidence,
            args.fallback_task,
            package_version=args.package_version,
        )
        result = describe_candidates(candidates)
        if args.selection_file:
            with args.selection_file.open("rb") as stream:
                raw = stream.read(8193)
            _require(len(raw) <= 8192, "selection_too_large")
            result = validate_selection(json.loads(raw, object_pairs_hook=_selection_object), candidates)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ReasonContractError as exc:
        print(json.dumps({"status": "rejected", "code": str(exc)}))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"status": "rejected", "code": "invalid_selection_file"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
