"""Bounded real-source reading and feedback-free projections for D-21 exploration."""

import gzip
import hashlib
import json
import re
from pathlib import Path

from .controlled_inputs import group_hash, project_helpsteer
from .replay import length_bucket, state_text
from .replay_laya import ReplayError, require
from .source_mapping import MappingError, digest, messages

VERSION = "m55-input-v1"
MAX_RECORD = 1048576
SOURCES = {
    "helpsteer3": (
        "nvidia/HelpSteer3",
        "f6d145777bcbde96137596340fab89793acd1031",
        "feedback/validation.jsonl.gz",
        5275463,
        "b86f71581a83f610ec5416029422f84ff1fc005e77b56c7634486ef9f6f12ceb",
    ),
    "wildfb": (
        "THU-KEG/WildFB",
        "0791dbc3101c6be7e0316cba1d8caf10c28917ad",
        "test.jsonl",
        86593611,
        "aeb337ad2d23e1791c10ab5a43bb7c8a593cf367882b34beb8274151a55631ff",
    ),
    "wildfeedback": (
        "microsoft/WildFeedback",
        "8b1a3e530b949d6aacfad6ba8912e209a05bc846",
        "sat_dsat_annotation.json",
        1615402501,
        "2caea8361756b35a512b2ebef93245df1e81d982a3602460f75a4445fc16d305",
    ),
}


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained(path, directory):
    path, directory = Path(path).resolve(), Path(directory).resolve()
    require(path.is_relative_to(directory) and path != directory, "outside_research_directory")
    return path


def parse_record(raw):
    try:
        row = json.loads(raw)
        return (row, None) if isinstance(row, dict) else (None, "record_not_object")
    except (UnicodeError, ValueError):
        return None, "record_invalid_json"


def array_records(stream, limit=MAX_RECORD):
    """One object at a time, including oversized objects; no whole-file JSON load."""
    begun = ended = quoted = escaped = False
    depth, size, index = 0, 0, 0
    data = bytearray()
    expect_value = True
    for chunk in iter(lambda: stream.read(65536), b""):
        for byte in chunk:
            if not begun:
                if chr(byte).isspace():
                    continue
                require(byte == 91, "source_not_json_array")
                begun = True
                continue
            if ended:
                require(chr(byte).isspace(), "source_trailing_data")
                continue
            if depth == 0:
                if chr(byte).isspace():
                    continue
                if byte == 93:
                    require(index == 0 or not expect_value, "source_invalid_array")
                    ended = True
                    continue
                if byte == 44:
                    require(not expect_value, "source_invalid_array")
                    expect_value = True
                    continue
                require(expect_value and byte == 123, "source_array_element_not_object")
                depth, size, quoted, escaped = 1, 1, False, False
                data = bytearray(b"{")
                continue
            size += 1
            if size <= limit:
                data.append(byte)
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (123, 91):
                depth += 1
            elif byte in (125, 93):
                depth -= 1
            if depth == 0:
                row, error = parse_record(data) if size <= limit else (None, "record_oversized")
                yield index, row, error
                index += 1
                expect_value = False
                data.clear()
    require(begun and ended and depth == 0, "source_truncated_array")


def source_rows(path, source):
    opener = gzip.open if source == "helpsteer3" else open
    with opener(path, "rb") as stream:
        if source == "wildfeedback":
            yield from array_records(stream)
            return
        index = 0
        while raw := stream.readline(MAX_RECORD + 1):
            if len(raw) > MAX_RECORD:
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(MAX_RECORD + 1)
                row, error = None, "record_oversized"
            else:
                row, error = parse_record(raw)
            yield index, row, error
            index += 1


def language(text):
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]", text))
    if zh > 20:
        return "mixed" if en > zh else "zh"
    return "en" if en > 20 else "unknown"


def projection(source, batch, row_id, target_id, context, answer, reference):
    context = messages(context)
    require(context[-1]["role"] == "user", "target_predecessor_not_user")
    require(isinstance(answer, str) and answer.strip(), "source_answer_invalid")
    state = state_text({"context": context, "answer": answer})
    identity = {
        "source": source,
        "revision": batch["revision"],
        "file_sha256": batch["sha256"],
        "row_id": row_id,
        "target_id": target_id,
    }
    evidence = ["request", "answer"]
    if re.search(r"(?m)^```[^\n]*\n[\s\S]+?\n```[ \t]*$", context[-1]["content"]):
        evidence.append("original_code")
    return identity | {
        "input_id": digest(identity),
        "mapping_version": VERSION,
        "state": state,
        "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "context": context,
        "answer": answer,
        "conversation_group_id": group_hash(context),
        "evidence_kinds": evidence,
        "input_utf8_bytes": len(state.encode()),
        "length_bucket": length_bucket(len(state.encode())),
        "language": language(context[-1]["content"]),
        "language_origin": "character_heuristic_v1",
        "task": "unknown",
        "reference": reference,
    }


def map_record(source, batch, row_id, row):
    """Return target-level outcomes; a rejected sibling never removes a valid target."""
    result, errors = [], []
    if source == "helpsteer3":
        for key in ("response1", "response2"):
            try:
                project_helpsteer(row, key)
                result.append(
                    projection(
                        source,
                        batch,
                        row_id,
                        key,
                        row["context"],
                        row[key],
                        {"feedback": row["feedback" + key[-1]], "origin": "evaluator"},
                    )
                )
            except (MappingError, ReplayError) as exc:
                errors.append({"target_id": key, "error": str(exc)})
        return result, errors
    try:
        require(source == "wildfb", "source_adapter_not_enabled")
        pair = messages(row.get("messages"))
        history = row.get("history")
        require(isinstance(history, list), "source_history_invalid")
        history = messages(history) if history else []
        feedback = messages([row.get("user_feedback")])[0]
        require(len(pair) == 2 and [m["role"] for m in pair] == ["user", "assistant"], "ambiguous_target")
        require(feedback["role"] == "user", "invalid_feedback_role")
        require(not history or history[-1]["role"] == "assistant", "history_target_mismatch")
        result.append(
            projection(
                source,
                batch,
                row_id,
                "messages/1",
                history + pair[:1],
                pair[1]["content"],
                {"feedback": [feedback["content"]], "origin": "original_user", "automated_label": row.get("label")},
            )
        )
    except (MappingError, ReplayError) as exc:
        errors.append({"target_id": None, "error": str(exc)})
    return result, errors


class WildFeedbackMapper:
    """The pinned file is ordered utterances, with explicit 0-based conversation resets."""

    def __init__(self):
        self.context, self.next_id, self.total_bytes, self.group_start = [], None, 0, None

    def map(self, batch, row_id, row):
        try:
            number, turn, role = row.get("UtterranceId"), row.get("TurnId"), row.get("Role")
            require(type(number) is int and type(turn) is int, "invalid_utterance_identity")
            if number == 0:
                self.context, self.next_id, self.total_bytes, self.group_start = [], 0, 0, row_id
            require(
                self.next_id is not None and number == self.next_id and turn == number // 2 + 1,
                "ambiguous_conversation_boundary",
            )
            expected = "User" if number % 2 == 0 else "Agent"
            require(role == expected, "utterance_role_mismatch")
            content = row.get("Content")
            require(isinstance(content, str) and content.strip(), "empty_utterance")
            self.total_bytes += len(content.encode())
            require(self.total_bytes <= MAX_RECORD, "conversation_oversized")
            result = []
            if role == "Agent":
                reference = {
                    "origin": "automated_annotation",
                    "feedback": [],
                    "feedback_row_id": row_id + 1,
                    "conversation_start_row": self.group_start,
                    "automated_annotation": {
                        k: row.get(k)
                        for k in (
                            "Satisfaction",
                            "Satisfaction-Justificiation",
                            "Disatisfaction",
                            "Dissatisfaction-Justificiation",
                            "Preceeding",
                        )
                    },
                }
                item = projection(
                    "wildfeedback", batch, row_id, f"utterance/{number}", list(self.context), content, reference
                )
                item["source_conversation_id"] = digest({"file": batch["sha256"], "start": self.group_start})
                result.append(item)
            self.context.append({"role": "user" if role == "User" else "assistant", "content": content})
            self.next_id += 1
            return result, []
        except (MappingError, ReplayError) as exc:
            self.context, self.next_id, self.total_bytes = [], None, 0
            return [], [{"target_id": None, "error": str(exc)}]
