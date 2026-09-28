"""Strict source bindings for M5-0a; records contain references, never source text."""

import hashlib
import json
import uuid

VERSION = "m5-source-map-v1"
REVISIONS = {
    "wildfb": "0791dbc3101c6be7e0316cba1d8caf10c28917ad",
    "helpsteer3": "f6d145777bcbde96137596340fab89793acd1031",
    "wildfeedback": "8b1a3e530b949d6aacfad6ba8912e209a05bc846",
}
DIAGNOSES = {"missing_category", "classification_error", "missing_context", "insufficient_evidence", "multiple_issues"}
TASKS = {"code_rewrite", "general", "other", "mixed", "unknown"}
LANGUAGES = {"zh", "en", "mixed", "other", "unknown"}


class MappingError(ValueError):
    """Only constant, non-content error codes may leave the mapper."""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def require(condition, code):
    if not condition:
        raise MappingError(code)


def reference(value):
    try:
        require(isinstance(value, str), "invalid_reference")
        return str(uuid.UUID(value))
    except ValueError:
        raise MappingError("invalid_reference") from None


def text_value(value):
    require(isinstance(value, str) and bool(value.strip()), "invalid_text")
    return value


def messages(value):
    require(isinstance(value, list) and bool(value), "invalid_messages")
    for message in value:
        require(isinstance(message, dict), "invalid_messages")
        require(set(message) == {"role", "content"}, "unsupported_message_fields")
        require(message.get("role") in ("system", "user", "assistant"), "invalid_role")
        text_value(message.get("content"))
    return [{"role": m["role"], "content": m["content"]} for m in value]


def pointer_value(row, pointer):
    require(isinstance(pointer, str) and pointer.startswith("/") and len(pointer) <= 256, "invalid_pointer")
    current = row
    try:
        for part in pointer[1:].split("/"):
            require("~" not in part.replace("~0", "").replace("~1", ""), "invalid_pointer")
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                require(part.isdecimal() and str(int(part)) == part, "invalid_pointer")
                current = current[int(part)]
            else:
                require(isinstance(current, dict), "invalid_pointer")
                current = current[part]
    except (KeyError, IndexError, TypeError, ValueError):
        raise MappingError("invalid_pointer") from None
    return current


def validate_review(value):
    if value is None:
        return {
            "review_ref": None,
            "reviewer_ref": None,
            "review_version": None,
            "diagnoses": None,
            "task": "unknown",
            "context_language": "unknown",
            "feedback_language": "unknown",
            "group_ref": None,
            "partition": "unassigned",
            "split_version": None,
            "screening_ref": None,
        }
    require(isinstance(value, dict), "invalid_review")
    defaults = validate_review(None)
    require(set(value) <= set(defaults), "invalid_review")
    result = defaults | value
    for key in ("review_ref", "reviewer_ref"):
        result[key] = reference(result[key])
    require(result["review_version"] == "m5-case-review-v1", "invalid_review_version")
    for key in ("group_ref", "split_version", "screening_ref"):
        if result[key] is not None:
            result[key] = reference(result[key])
    diagnoses = result["diagnoses"]
    require(isinstance(diagnoses, list) and all(isinstance(d, str) for d in diagnoses), "invalid_diagnoses")
    require(set(diagnoses) <= DIAGNOSES and len(set(diagnoses)) == len(diagnoses), "invalid_diagnoses")
    require(result["task"] in TASKS, "invalid_task")
    require(result["context_language"] in LANGUAGES and result["feedback_language"] in LANGUAGES, "invalid_language")
    require(result["partition"] in ("exploration", "holdout", "unassigned"), "invalid_partition")
    if result["partition"] != "unassigned":
        require(result["group_ref"] is not None and result["split_version"] is not None, "missing_split_review")
    result["diagnoses"] = sorted(diagnoses)
    return result


def make_record(source, row_id, target_key, context, answer, feedback, paths, review, *, origin, labels=()):
    identity = {k: source[k] for k in ("source", "revision", "config", "split", "data_sha256")}
    identity |= {"row_id": row_id, "target_key": target_key, "target_pointer": paths["target"]}

    def ref(pointer, value):
        return {"row_id": row_id, "pointer": pointer, "sha256": digest(value)}

    context_refs = [ref(p, m) for p, m in zip(paths["context"], context, strict=True)]
    target_ref = ref(paths["target"], answer)
    feedback_refs = [ref(p, f) for p, f in zip(paths["feedback"], feedback, strict=True)]
    record = identity | {
        "record_id": digest(identity),
        "mapping_version": VERSION,
        "mode": "synthetic",
        "filter_version": "m5-source-filter-v1",
        "source_file_ref": source["data_path"],
        "source_format": source["format"],
        "review_file_sha256": source["reviews_sha256"],
        "target_turn_index": len(context),
        "target_response_index": int(target_key[-1]) if target_key.startswith("response") else None,
        "target_version": target_ref["sha256"],
        "context_sha256": digest(context),
        "context_refs": context_refs,
        "target_ref": target_ref,
        "prediction_refs": {"context": context_refs, "target": target_ref},
        "feedback_refs": feedback_refs,
        "feedback_origin": origin,
        "label_refs": [ref(p, v) | {"origin": "automated"} for p, v in labels],
        "source_id_ref": paths.get("source_id"),
        "input_utf8_bytes": sum(len(m["content"].encode("utf-8")) for m in context) + len(answer.encode("utf-8")),
        "feedback_count": len(feedback),
        "review": validate_review(review),
        "exclusion_reasons": [],
    }
    r = record["review"]
    record["ready_for_replay"] = bool(
        r["review_ref"] and r["screening_ref"] and r["group_ref"] and r["partition"] != "unassigned"
    )
    return record


def map_helpsteer3(source, row_id, row, review, target_key):
    context = messages(row.get("context"))
    require(context[-1]["role"] == "user", "context_must_end_with_user")
    answer = text_value(row.get(target_key))
    feedback_key = "feedback" + target_key[-1]
    feedback = row.get(feedback_key)
    require(isinstance(feedback, list) and bool(feedback), "invalid_feedback_array")
    for item in feedback:
        text_value(item)
    return make_record(
        source,
        row_id,
        target_key,
        context,
        answer,
        feedback,
        {
            "context": [f"/context/{i}" for i in range(len(context))],
            "target": f"/{target_key}",
            "feedback": [f"/{feedback_key}/{i}" for i in range(len(feedback))],
        },
        review,
        origin="evaluator",
    )


def map_conversation(source, row_id, row, binding, review):
    require(isinstance(binding, dict), "missing_linkage")
    allowed = {"schema_review_ref", "linkage_ref", "target_index", "conversation_pointer"}
    require(set(binding) <= allowed, "invalid_linkage")
    schema_ref = reference(binding.get("schema_review_ref"))
    linkage_ref = reference(binding.get("linkage_ref"))
    is_wildfb = source["source"] == "wildfb"
    pointer = "/messages" if is_wildfb else binding.get("conversation_pointer")
    if is_wildfb:
        require(binding.get("conversation_pointer", "/messages") == "/messages", "invalid_linkage")
    conversation = messages(pointer_value(row, pointer))
    index = binding.get("target_index")
    require(type(index) is int and 0 < index < len(conversation) - 1, "invalid_target_index")
    require(conversation[index - 1]["role"] == "user", "invalid_target_predecessor")
    require(conversation[index]["role"] == "assistant", "invalid_target_role")
    require(conversation[index + 1]["role"] == "user", "invalid_feedback_role")
    feedback = conversation[index + 1]["content"]
    labels = []
    if is_wildfb:
        require(text_value(row.get("user_feedback")) == feedback, "feedback_target_mismatch")
        require(type(row.get("label")) is int and 1 <= row["label"] <= 4, "invalid_automated_label")
        labels = [("/label", row["label"])]
    record = make_record(
        source,
        row_id,
        "conversation",
        conversation[:index],
        conversation[index]["content"],
        [feedback],
        {
            "context": [f"{pointer}/{i}" for i in range(index)],
            "target": f"{pointer}/{index}/content",
            "feedback": [f"{pointer}/{index + 1}/content"],
            "source_id": "/id" if "id" in row else None,
        },
        review,
        origin="original_user",
        labels=labels,
    )
    record |= {"schema_review_ref": schema_ref, "linkage_ref": linkage_ref}
    return record


def map_row(source, row_id, row, annotations):
    """Return all target outcomes; a bad response must not suppress its sibling."""
    records, rejected = [], []
    target_keys = ("response1", "response2") if source["source"] == "helpsteer3" else ("conversation",)
    try:
        require(isinstance(row, dict), "invalid_row")
        require(isinstance(annotations, dict) and set(annotations) <= {"linkage", "targets"}, "invalid_annotations")
        targets = annotations.get("targets", {})
        require(isinstance(targets, dict) and set(targets) <= set(target_keys), "unexpected_review_target")
    except MappingError as exc:
        return [], [{"row_id": row_id, "target_key": None, "code": str(exc)}]
    for target_key in target_keys:
        try:
            if source["source"] == "helpsteer3":
                result = map_helpsteer3(source, row_id, row, targets.get(target_key), target_key)
            else:
                result = map_conversation(source, row_id, row, annotations.get("linkage"), targets.get(target_key))
            records.append(result)
        except MappingError as exc:
            rejected.append({"row_id": row_id, "target_key": target_key, "code": str(exc)})
        except (TypeError, ValueError, KeyError):
            rejected.append({"row_id": row_id, "target_key": target_key, "code": "invalid_structure"})
    return records, rejected


def check_splits(records):
    """Fail closed for exact overlap; near-duplicate grouping remains human review."""
    groups = {}
    for record in records:
        review = record["review"]
        keys = [("context", record["context_sha256"]), ("target", record["target_version"])]
        if review["group_ref"]:
            keys.append(("reviewed", review["group_ref"]))
        for key in keys:
            groups.setdefault(key, []).append(record)
    for key, members in groups.items():
        partitions = {r["review"]["partition"] for r in members} - {"unassigned"}
        versions = {r["review"]["split_version"] for r in members} - {None}
        if len(partitions) > 1 or (key[0] == "reviewed" and len(versions) > 1):
            for record in members:
                record["ready_for_replay"] = False
                if "split_conflict" not in record["exclusion_reasons"]:
                    record["exclusion_reasons"].append("split_conflict")
