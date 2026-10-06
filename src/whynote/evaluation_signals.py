"""Frozen source signals for offline evaluation; never defect labels or model input."""

import gzip
import json
import re
from collections import Counter

from . import two_stage_batch as batch
from .explore_inputs import MAX_RECORD, array_chunks

VERSION = "source-feedback-groups-v1"
GROUPS = ("negative_signal", "positive_signal", "mixed_signal", "unclear_signal")
LEVEL = re.compile(r"\AThe response is (not|slightly|partially|mostly|perfectly) helpful(?=[\s.,;:!?]|$)")
WF_FIELDS = {"UtterranceId", "TurnId", "Role", "Preceeding", "Satisfaction", "Disatisfaction"}


def signal(group, reason, labels):
    return {"group": group, "reason": reason, "labels": labels}


def helpsteer(feedback, *, complete):
    values = feedback if isinstance(feedback, list) else []
    grades = [match[1] if isinstance(item, str) and (match := LEVEL.match(item)) else None for item in values]
    labels = {
        "evaluation_count": len(values),
        "grades": grades,
        "grade_counts": dict(Counter(grade for grade in grades if grade is not None)),
        "all_mostly_or_perfectly": len(grades) == 3 and all(g in {"mostly", "perfectly"} for g in grades),
    }
    if not complete:
        return signal("unclear_signal", "feedback_binding_or_completeness_unverified", labels)
    if len(values) != 3:
        return signal("unclear_signal", "incomplete_evaluation_count", labels)
    if None in grades:
        return signal("unclear_signal", "invalid_structured_helpfulness_prefix", labels)
    positive, negative = "perfectly" in grades, bool({"not", "slightly"} & set(grades))
    if positive and negative:
        return signal("mixed_signal", "explicit_positive_and_negative_evaluations", labels)
    if set(grades) == {"perfectly"}:
        return signal("positive_signal", "all_perfectly_helpful", labels)
    if set(grades) <= {"not", "slightly"}:
        return signal("negative_signal", "all_not_or_slightly_helpful", labels)
    return signal("unclear_signal", "intermediate_or_nondecisive_evaluation_combination", labels)


def wildfb(label, *, linked):
    valid = type(label) is int and label in {1, 2, 3, 4}
    labels = {"automated_label": label if valid else None, "valid": valid}
    if not linked:
        return signal("unclear_signal", "feedback_target_link_unverified", labels)
    if not valid:
        return signal("unclear_signal", "missing_or_invalid_automated_label", labels)
    return signal("negative_signal" if label in {1, 2} else "positive_signal", "source_pair_label_verified", labels)


def wildfeedback(target, following, *, same_segment):
    following = following if isinstance(following, dict) else {}
    sat, dsat = following.get("Satisfaction"), following.get("Disatisfaction")
    valid = type(sat) is bool and type(dsat) is bool
    labels = {"sat": sat if type(sat) is bool else None, "dsat": dsat if type(dsat) is bool else None}
    if not following:
        return signal("unclear_signal", "no_following_user_annotation", labels)
    linked = (
        same_segment
        and target.get("Role") == "Agent"
        and following.get("Role") == "User"
        and type(target.get("UtterranceId")) is int
        and type(target.get("TurnId")) is int
        and type(following.get("UtterranceId")) is int
        and type(following.get("TurnId")) is int
        and following["UtterranceId"] == target["UtterranceId"] + 1
        and following["TurnId"] == target["TurnId"] + 1
    )
    if not linked:
        return signal("unclear_signal", "following_annotation_cross_session_or_turn", labels)
    if following.get("Preceeding") != "YES":
        return signal("unclear_signal", "following_topic_relation_unverified", labels)
    if not valid:
        return signal("unclear_signal", "missing_or_invalid_sat_dsat", labels)
    group = (
        "mixed_signal"
        if sat and dsat
        else "positive_signal"
        if sat
        else "negative_signal"
        if dsat
        else "unclear_signal"
    )
    return signal(group, "following_user_signal" if sat or dsat else "no_explicit_sat_dsat", labels)


def value_end(raw, start):
    """Skip JSON values lexically; unselected source bodies are never decoded."""
    stack, quoted, escaped = [], False, False
    for i in range(start, len(raw)):
        c = raw[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == 92:
                escaped = True
            elif c == 34:
                quoted = False
                if not stack:
                    return i + 1
        elif c == 34:
            quoted = True
        elif c in (123, 91):
            stack.append(c)
        elif c in (125, 93):
            if not stack:
                return i
            batch.require(stack.pop() == (123 if c == 125 else 91), "source_json_brackets_invalid")
            if not stack:
                return i + 1
        elif not stack and c in b", \t\r\n":
            return i
    batch.require(not quoted and not stack, "source_json_truncated")
    return len(raw)


def select_fields(raw, fields):
    """Decode only approved top-level fields, rejecting duplicate source keys."""
    raw = raw.strip()
    batch.require(raw.startswith(b"{") and raw.endswith(b"}"), "source_record_not_object")
    cursor, seen, selected = 1, set(), {}
    while True:
        while cursor < len(raw) and raw[cursor] in b" \t\r\n":
            cursor += 1
        if raw[cursor] == 125:
            batch.require(cursor == len(raw) - 1, "source_json_trailing_data")
            return selected
        batch.require(raw[cursor] == 34, "source_json_key_invalid")
        end = value_end(raw, cursor)
        key = json.loads(raw[cursor:end])
        batch.require(key not in seen, "duplicate_source_field")
        seen.add(key)
        cursor = end
        while raw[cursor] in b" \t\r\n":
            cursor += 1
        batch.require(raw[cursor] == 58, "source_json_colon_missing")
        cursor += 1
        while raw[cursor] in b" \t\r\n":
            cursor += 1
        end = value_end(raw, cursor)
        if key in fields:
            selected[key] = json.loads(raw[cursor:end])
        cursor = end
        while raw[cursor] in b" \t\r\n":
            cursor += 1
        batch.require(raw[cursor] in (44, 125), "source_json_delimiter_invalid")
        if raw[cursor] == 44:
            cursor += 1
            batch.require(raw[cursor:].strip() != b"}", "source_json_trailing_comma")


def selected_source_rows(path, source, selectors, check):
    """Read only selected rows/fields; excluded targets never select body fields."""
    if not selectors:
        return {}
    result, last = {}, max(selectors)
    opener = gzip.open if source == "helpsteer3" else open
    check()
    with opener(path, "rb") as stream:
        iterator = array_chunks(stream) if source == "wildfeedback" else bounded_lines(stream)
        for index, raw in iterator:
            check()
            if index in selectors:
                if raw is None or len(raw) > MAX_RECORD:
                    result[index] = {"_error": "source_row_oversized"}
                else:
                    try:
                        result[index] = select_fields(raw, selectors[index])
                    except (ValueError, UnicodeError, IndexError, batch.Closed):
                        result[index] = {"_error": "source_row_schema_invalid"}
            if index >= last:
                break
    return result


def bounded_lines(stream):
    index = 0
    while raw := stream.readline(MAX_RECORD + 1):
        oversized = len(raw) > MAX_RECORD
        if oversized:
            while not raw.endswith(b"\n"):
                raw = stream.readline(MAX_RECORD + 1)
                if not raw:
                    break
        yield index, None if oversized else raw
        index += 1
