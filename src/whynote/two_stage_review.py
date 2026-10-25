"""Frozen group sampling and append-only review contracts; no inferred human labels."""

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

VERSION = "jev-human-review-v1"
SAMPLING_VERSION = "jev-group-sampling-v1"
DECISIONS = {"not_reviewed", "present", "absent", "insufficient_evidence", "out_of_scope"}
EXPERTISE = {"not_reported", "qualified", "uncertain", "unfamiliar"}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def group_cases(cases, *, near_similarity=0.9):
    """Union case/lineage/conversation aliases and near-duplicate first user prompts.

    Bodies are used transiently, never returned. A shared prompt conservatively
    links both HelpSteer answers and conversation prefixes, even across sources.
    Explicit near/parent aliases preserve known rewrites with low lexical overlap.
    """
    require(0.8 <= near_similarity <= 1, "invalid_near_similarity")
    ids = [case["target_id"] for case in cases]
    require(len(ids) == len(set(ids)), "duplicate_target")
    parent = {key: key for key in ids}

    def root(key):
        while key != parent[key]:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        a, b = sorted((root(a), root(b)))
        parent[b] = a

    aliases, inverted, shingles = {}, defaultdict(list), {}
    for case in sorted(cases, key=lambda c: c["target_id"]):
        key = case["target_id"]
        prompt = normalized(case["group_prompt"])
        require(bool(prompt) and bool(case["group_aliases"]), "missing_group_identity")
        for alias in [*case["group_aliases"], "prompt:" + text_hash(prompt)]:
            require(isinstance(alias, str) and bool(alias), "invalid_group_alias")
            if alias in aliases:
                union(key, aliases[alias])
            aliases[alias] = key
        tokens = re.findall(r"[\u3400-\u9fff]|[^\W_]+", prompt)
        grams = {tuple(tokens[i : i + 5]) for i in range(len(tokens) - 4)}
        if not grams:
            continue
        candidates = {other for gram in grams for other in inverted[gram]}
        for other in candidates:
            prior = shingles[other]
            if min(len(grams), len(prior)) / max(len(grams), len(prior)) < near_similarity:
                continue
            if len(grams & prior) / len(grams | prior) >= near_similarity:
                union(key, other)
        shingles[key] = grams
        for gram in grams:
            inverted[gram].append(key)
    components = defaultdict(list)
    for key in ids:
        components[root(key)].append(key)
    return {key: fingerprint(sorted(members)) for members in components.values() for key in members}


def sample_manifest(cases, groups, binding, *, seed="jev-review-20261006-v1", calibration_count=400):
    """Diagnostic selection never enters calibration; one random target per group.

    Calibration uses deterministic source-stratified random allocation. Internal
    confirmation uses all remaining groups, equally weighted, fixed before labels.
    All known runs of a target inherit its group and partition.
    """
    require(type(calibration_count) is int and calibration_count > 0, "invalid_sample_count")
    require(set(groups) == {c["target_id"] for c in cases}, "group_population_mismatch")
    by_group = defaultdict(list)
    for case in cases:
        by_group[groups[case["target_id"]]].append(case)

    def rank(*parts):
        return fingerprint([seed, *parts])

    diagnostics = {g for g, members in by_group.items() if any(c.get("diagnostic_flags") for c in members)}
    representatives = {
        g: min(members, key=lambda c: rank("representative", g, c["target_id"])) for g, members in by_group.items()
    }
    strata = defaultdict(list)
    for group, case in representatives.items():
        if group not in diagnostics:
            strata[case["source"]].append(group)
    for source in strata:
        strata[source].sort(key=lambda g: rank("calibration", g))
    selected, allocation = set(), Counter()
    available = sum(map(len, strata.values()))
    # Proportional allocation with deterministic residual balancing.
    for _ in range(min(calibration_count, available)):
        source = max(
            (s for s in strata if allocation[s] < len(strata[s])),
            key=lambda s: (len(strata[s]) / available - allocation[s] / max(1, len(selected)), s),
        )
        selected.add(strata[source][allocation[source]])
        allocation[source] += 1
    rows = []
    for group in sorted(by_group, key=lambda g: rank("display", g)):
        partition = "diagnostic" if group in diagnostics else "calibration" if group in selected else "confirmation"
        representative = representatives[group]
        for case in sorted(by_group[group], key=lambda c: c["target_id"]):
            rows.append(
                {
                    "target_id": case["target_id"],
                    "group_id": group,
                    "partition": partition,
                    "selected": bool(case.get("diagnostic_flags"))
                    if partition == "diagnostic"
                    else case["target_id"] == representative["target_id"],
                    "source": case["source"],
                    "unit_aliases": sorted(
                        {
                            fingerprint(alias)
                            for member in by_group[group]
                            for alias in [
                                *member["group_aliases"],
                                "prompt:" + text_hash(normalized(member["group_prompt"])),
                            ]
                        }
                    ),
                    "input_version": case["input_version"],
                    "input_sha256": case["input_sha256"],
                    "diagnostic_flags": case.get("diagnostic_flags", []),
                    "exposure": case.get("exposure", ["historical_predictions_explored"]),
                }
            )
    return {
        "schema_version": SAMPLING_VERSION,
        "seed": seed,
        "binding": binding,
        "grouping": {"near_jaccard": 0.9, "shingle_tokens": 5, "conservative_prompt_union": True},
        "calibration_requested_groups": calibration_count,
        "calibration_selected_groups": len(selected),
        "calibration_shortfall": max(0, calibration_count - len(selected)),
        "confirmation_scope": "internal_exposed_population",
        "formal_independent_validation": "PENDING_NEW_UNEXPOSED_GROUPS",
        "primary_run": binding["primary_run"],
        "confirmation_sampling": "equal_group_weight_one_preselected_target_all_remaining_groups",
        "confirmation_fixed_groups": len(by_group) - len(diagnostics) - len(selected),
        "calibration_strata": dict(allocation),
        "rows": rows,
        "human_labels": "PENDING",
        "selected_thresholds": None,
    }


def blind_case(case, catalog):
    """Explicit allowlist: no source labels, selection flags, predictions or scores."""
    return {
        **{k: case[k] for k in ("target_id", "group_id", "input_version", "input_sha256", "documents")},
        **{k: case[k] for k in ("foundation", "materials", "route_input") if k in case},
        "schema_version": VERSION,
        "catalog_sha256": fingerprint(catalog),
        "tasks": catalog["tasks"],
        "domains": catalog["domains"],
        "reasons": [
            {k: reason[k] for k in ("id", "label", "criteria", "evidence_paths", "tasks", "domains")}
            for reason in catalog["reasons"]
        ],
    }


def empty_label(case, catalog, reviewer_id="", *, reviewer_kind="human", round_id="initial"):
    return {
        "schema_version": VERSION,
        **{k: case[k] for k in ("target_id", "group_id", "input_version", "input_sha256")},
        "catalog_sha256": fingerprint(catalog),
        "reviewer": {"id": reviewer_id, "kind": reviewer_kind},
        "round_id": round_id,
        "phase": "independent_input_review",
        "exposure": "not_reported",
        "independent_attestation": False,
        "created_at": timestamp(),
        "routes": {
            axis: {"status": "not_reviewed", "accepted": [], "evidence": [], "basis": ""} for axis in ("task", "domain")
        },
        "reasons": {
            reason["id"]: {
                "decision": "not_reviewed",
                "evidence": [],
                "basis": "",
                "missing": [],
                "expertise": "not_reported",
            }
            for reason in catalog["reasons"]
        },
        "disagreement": "unreviewed",
    }


def validate_span(span, documents):
    require(isinstance(span, dict) and set(span) == {"document", "start", "end", "sha256"}, "invalid_span")
    require(span["document"] in documents, "unknown_evidence_document")
    text = documents[span["document"]]["text"]
    require(type(span["start"]) is int and type(span["end"]) is int, "invalid_offsets")
    require(0 <= span["start"] < span["end"] <= len(text), "evidence_out_of_range")
    require(span["sha256"] == text_hash(text), "evidence_document_changed")


def validate_label(label, case, catalog, *, complete=False):
    template = empty_label(case, catalog)
    require(isinstance(label, dict) and set(label) == set(template), "invalid_label_fields")
    for key in ("schema_version", "target_id", "group_id", "input_version", "input_sha256", "catalog_sha256", "phase"):
        require(label[key] == template[key], "label_binding_mismatch:" + key)
    reviewer = label["reviewer"]
    require(isinstance(reviewer, dict) and set(reviewer) == {"id", "kind"}, "invalid_reviewer")
    require(reviewer["kind"] in {"human", "ai"}, "invalid_reviewer_kind")
    require(isinstance(reviewer["id"], str) and 0 < len(reviewer["id"].strip()) <= 100, "reviewer_identity_required")
    require(isinstance(label["round_id"], str) and bool(label["round_id"].strip()), "review_round_required")
    require(label["exposure"] in {"not_reported", "none_reported", "possible", "predictions_seen"}, "invalid_exposure")
    require(type(label["independent_attestation"]) is bool, "invalid_attestation")
    require(label["disagreement"] in {"unreviewed", "agreed", "disputed", "adjudicated"}, "invalid_disagreement")
    try:
        date = datetime.fromisoformat(label["created_at"])
        require(date.tzinfo is not None, "review_time_requires_timezone")
    except (TypeError, ValueError):
        raise ValueError("invalid_review_time") from None
    require(set(label["routes"]) == {"task", "domain"}, "invalid_route_labels")
    for axis, route in label["routes"].items():
        require(set(route) == {"status", "accepted", "evidence", "basis"}, "invalid_route_label")
        require(route["status"] in {"not_reviewed", "reviewed", "cannot_determine"}, "invalid_route_status")
        options = catalog["tasks" if axis == "task" else "domains"]
        require(
            isinstance(route["accepted"], list) and len(set(route["accepted"])) == len(route["accepted"]),
            "duplicate_route",
        )
        require(set(route["accepted"]) <= set(options), "unknown_route_label")
        require((route["status"] == "reviewed") == bool(route["accepted"]), "route_status_conflict")
        if complete:
            require(route["status"] != "not_reviewed", "route_not_reviewed")
        if route["status"] != "not_reviewed":
            require(bool(route["basis"].strip()), "route_basis_required")
        if route["status"] == "reviewed":
            require(bool(route["evidence"]), "route_evidence_required")
    require(set(label["reasons"]) == {r["id"] for r in catalog["reasons"]}, "reason_catalog_incomplete")
    for row in [*label["routes"].values(), *label["reasons"].values()]:
        require(isinstance(row["basis"], str) and len(row["basis"]) <= 4000, "invalid_review_basis")
        require(isinstance(row["evidence"], list), "invalid_evidence_list")
        for span in row["evidence"]:
            validate_span(span, case["documents"])
    for reason in label["reasons"].values():
        require(set(reason) == {"decision", "evidence", "basis", "missing", "expertise"}, "invalid_reason_label")
        require(reason["decision"] in DECISIONS and reason["expertise"] in EXPERTISE, "invalid_reason_decision")
        require(
            isinstance(reason["missing"], list) and all(isinstance(x, str) and x.strip() for x in reason["missing"]),
            "invalid_missing_material",
        )
        if reason["decision"] != "not_reviewed":
            require(bool(reason["basis"].strip()), "review_basis_required")
            require(reason["expertise"] != "not_reported", "review_expertise_required")
        if reason["decision"] in {"present", "absent", "out_of_scope"}:
            require(bool(reason["evidence"]), "review_evidence_required")
            require(reason["expertise"] == "qualified", "unfamiliar_is_not_material_insufficiency")
        if reason["decision"] == "insufficient_evidence":
            require(bool(reason["missing"]), "missing_material_required")
        if complete:
            require(reason["decision"] != "not_reviewed", "reason_not_reviewed")
    if complete:
        require(
            label["exposure"] != "not_reported" and label["independent_attestation"],
            "independent_review_attestation_required",
        )
    return label


def review_requirement(initial, *, seed, other_fraction=0.2):
    """All positives/insufficiency, plus a frozen random sample of other items."""
    required = []
    for key, row in initial["reasons"].items():
        draw = int(fingerprint([seed, initial["target_id"], key])[:16], 16) / 16**16
        if row["decision"] in {"present", "insufficient_evidence"} or draw < other_fraction:
            required.append(key)
    return required


def reconcile_labels(labels, *, formal=False, adjudication=None):
    """Reconcile co-equal reviewer opinions without erasing their provenance."""
    require(bool(labels), "labels_missing")
    bindings = {
        fingerprint({k: x[k] for k in ("target_id", "group_id", "input_sha256", "catalog_sha256")}) for x in labels
    }
    require(len(bindings) == 1, "review_input_mismatch")
    reviewers = [*labels, *([adjudication] if adjudication is not None else [])]
    identities = {(x["reviewer"]["kind"], x["reviewer"]["id"]) for x in reviewers}
    independent = len(identities) >= 2 and all(
        x["independent_attestation"] and x["exposure"] == "none_reported" for x in reviewers
    )
    complete = all(
        all(r["decision"] != "not_reviewed" for r in x["reasons"].values())
        and all(r["status"] != "not_reviewed" for r in x["routes"].values())
        for x in labels
    )
    conflicts = [key for key in labels[0]["reasons"] if len({x["reasons"][key]["decision"] for x in labels}) > 1]
    route_conflicts = [
        axis
        for axis in ("task", "domain")
        if len({fingerprint([x["routes"][axis]["status"], sorted(x["routes"][axis]["accepted"])]) for x in labels}) > 1
    ]
    final = None
    if complete and not conflicts and not route_conflicts:
        final = labels[0]
    if adjudication is not None:
        require(
            adjudication["reviewer"]["kind"] in {"human", "ai"}
            and adjudication["disagreement"] == "adjudicated"
            and adjudication["independent_attestation"]
            and adjudication["exposure"] == "none_reported",
            "coequal_adjudicator_required",
        )
        require(
            adjudication["target_id"] == labels[0]["target_id"]
            and adjudication["input_sha256"] == labels[0]["input_sha256"],
            "adjudication_binding_mismatch",
        )
        require(
            all(r["decision"] != "not_reviewed" for r in adjudication["reasons"].values())
            and all(r["status"] != "not_reviewed" for r in adjudication["routes"].values()),
            "adjudication_incomplete",
        )
        final = adjudication
    if independent:
        evidence_level = "coequal_independent_reviewers"
    elif len(identities) == 1:
        evidence_level = "single_ai" if next(iter(identities))[0] == "ai" else "single_human"
    else:
        evidence_level = "multiple_reviewers_not_independent"
    return {
        "raw_labels": labels,
        "final_label": final,
        "reason_conflicts": conflicts,
        "route_conflicts": route_conflicts,
        "evidence_level": evidence_level,
        "status": "READY" if final and (independent and complete or not formal) else "INSUFFICIENT_EVIDENCE",
        "formal_independent": bool(final and independent and complete),
    }
