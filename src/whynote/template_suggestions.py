"""Bounded synthetic presentation; originals stay out of the event ledger."""

import hashlib
import re

from .task_reasons import load_reasons, prepare_candidates, validate_selection

UI_VERSION = "m5-template-mock-v1"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(question, answer, object_version, fixture):
    if (
        not isinstance(fixture, dict)
        or set(fixture) != {"tasks", "reason_ids", "outcome", "citations"}
        or not isinstance(fixture["citations"], dict)
        or any(not isinstance(text, str) or len(text.encode("utf-8")) > 8192 for text in (question, answer))
    ):
        raise ValueError("Invalid synthetic template fixture")
    evidence = [name for name, text in (("request", question), ("answer", answer)) if text.strip()]
    if re.search(r"(?m)^```[^\n]*\n[\s\S]+?\n```[ \t]*$", question):
        evidence.append("original_code")
    candidates = prepare_candidates(fixture["tasks"], evidence)
    selection = {
        "candidate_set_id": candidates.candidate_set_id,
        "outcome": fixture["outcome"],
        "reason_ids": fixture["reason_ids"],
    }
    selected = validate_selection(selection, candidates)
    reasons = {r.reason_id: r for r in load_reasons()}
    cards, metadata = [], []
    for reason_id in selected["reason_ids"]:
        quotes, references = [], []
        selectors = fixture["citations"].get(reason_id, [])
        if not isinstance(selectors, list) or len(selectors) > 2:
            raise ValueError("Invalid citation list")
        for selector in selectors:
            if not isinstance(selector, dict) or set(selector) != {"source", "start", "end", "source_sha256"}:
                continue
            source = selector["source"]
            if source not in ("request", "answer"):
                continue
            text = question if source == "request" else answer
            start, end = selector["start"], selector["end"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(text)
                or end - start > 1200
                or selector["source_sha256"] != digest(text)
            ):
                continue
            quote = text[start:end]
            ref = {**selector, "object_version": object_version, "quote_sha256": digest(quote)}
            references.append(ref)
            quotes.append({"source": source, "text": quote})
        reason = reasons[reason_id]
        cards.append(
            {
                "reason_id": reason_id,
                "label": reason.label,
                "template": reason.template if quotes else None,
                "quotes": quotes,
            }
        )
        metadata.append({"reason_id": reason_id, "references": references})
    return candidates, selection, cards, {"ui_version": UI_VERSION, "cards": metadata}


def validate_presentation(value, reason_ids, object_version):
    """Reject caller text or extra metadata before any durable event write."""
    if (
        not isinstance(value, dict)
        or set(value) != {"ui_version", "cards"}
        or value["ui_version"] != UI_VERSION
        or not isinstance(value["cards"], list)
        or len(value["cards"]) != len(reason_ids)
    ):
        raise ValueError("Invalid presentation")
    for card, reason_id in zip(value["cards"], reason_ids, strict=True):
        if (
            not isinstance(card, dict)
            or set(card) != {"reason_id", "references"}
            or card["reason_id"] != reason_id
            or not isinstance(card["references"], list)
            or len(card["references"]) > 2
        ):
            raise ValueError("Invalid presentation card")
        for ref in card["references"]:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"source", "start", "end", "source_sha256", "quote_sha256", "object_version"}
                or ref["source"] not in ("request", "answer")
                or ref["object_version"] != object_version
                or type(ref["start"]) is not int
                or type(ref["end"]) is not int
                or not 0 <= ref["start"] < ref["end"] <= 8192
                or ref["end"] - ref["start"] > 1200
                or any(
                    not isinstance(ref[k], str) or not re.fullmatch(r"[0-9a-f]{64}", ref[k])
                    for k in ("source_sha256", "quote_sha256")
                )
            ):
                raise ValueError("Invalid presentation reference")
