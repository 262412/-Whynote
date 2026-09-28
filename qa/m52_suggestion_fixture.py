"""Reproduce M5-2a synthetic receipts using the existing research fixture and report."""

import argparse
import json
import runpy
from pathlib import Path
from unittest.mock import patch

from whynote import suggestions
from whynote.research_report import report
from whynote.task_reasons import prepare_candidates

FIXTURE = runpy.run_path(str(Path(__file__).with_name("s1_research_fixture.py")))
ref = FIXTURE["ref"]


def build_fixture(directory):
    study = FIXTURE["Fixture"](directory)
    study.config.update(suggestion_research_enabled=True, suggestion_model_revision="a" * 40)
    candidates = prepare_candidates(["code_rewrite"], ["request", "answer", "original_code"])
    with patch("whynote.s1.time.time", lambda: study.clock), patch("whynote.store._now", study.now):
        for index, (source, operation) in enumerate(
            [
                ("self_natural", "yes"),
                ("scripted", "no"),
                ("scripted", "none_matched"),
                ("scripted", "skip"),
                ("scripted", "close"),
                ("scripted", "decline"),
                ("public_replay", "unknown"),
                ("public_replay", "no_match"),
                ("public_replay", "missing_render"),
                ("public_replay", "missing_generation"),
            ]
        ):
            name = f"m52-{index}"
            chat = study.chat(name, source)
            answer = study.answer(chat, name)
            target = study.store.qualify(chat, answer.body)
            event_id = study.store.create_action(
                study.principal, target, {"channel": "openwebui-s1", "interaction_contract": "manual-v1"}, name
            )["event_id"]
            if operation == "missing_generation":
                continue
            outcome = operation if operation in {"unknown", "no_match"} else "suggested"
            selection = {
                "candidate_set_id": candidates.candidate_set_id,
                "outcome": outcome,
                "reason_ids": ["code.behavior_changed", "general.style"] if outcome == "suggested" else [],
            }
            record_id = suggestions.generate(
                study.store,
                study.principal,
                event_id,
                ref(name + "-g"),
                ref(name),
                candidates,
                selection,
                display_id=ref(name + "-d"),
            )
            binding = next(
                e["payload"]["binding"]
                for e in study.store.get_events(study.principal, event_id)
                if e["record_id"] == record_id
            )
            if outcome != "suggested" or operation == "missing_render":
                continue
            suggestions.render(study.store, study.principal, event_id, ref(name + "-r"), ref(name + "-d"), binding)
            response = suggestions.respond(
                study.store,
                study.principal,
                event_id,
                ref(name + "-response"),
                ref(name),
                ref(name + "-d"),
                operation,
                "code.behavior_changed" if operation in {"yes", "no"} else None,
            )
            if operation == "yes":
                suggestions.respond(
                    study.store,
                    study.principal,
                    event_id,
                    ref(name + "-correct"),
                    ref(name),
                    ref(name + "-d"),
                    "correct",
                    "general.style",
                    response,
                )
                suggestions.invalidate(study.store, study.principal, event_id, ref(name + "-invalidate"), ref(name))
                study.store.retract_action(study.principal, event_id, name + "-undo")
        study.clock += suggestions.TTL_SECONDS
        result = report(study.store.path, study.principal, "2026-09-01T00:00:00Z", study.now())
    # Only metadata is exported; the database stays in the caller's new local directory.
    (Path(directory) / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New synthetic output directory; must not exist")
    args = parser.parse_args()
    result = build_fixture(args.output)
    print(json.dumps({source: group["suggestions"] for source, group in result["groups"].items()}, indent=2))


if __name__ == "__main__":
    main()
