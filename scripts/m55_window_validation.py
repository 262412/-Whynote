"""One bounded local research run; raw state stays in the admitted research directory."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from whynote.controlled_inputs import load_tokenizer, measure_budget
from whynote.controlled_replay import source_hash
from whynote.explore_inputs import file_hash
from whynote.explore_prepare import read_plan
from whynote.explore_run import append, datetime_valid
from whynote.replay import state_text
from whynote.replay_laya import require
from whynote.replay_runtime import model_lock, trusted_command
from whynote.self_review_contract import budget_ok
from whynote.source_mapping import digest
from whynote.window_diagnostics import POLICY, QUESTION_VERSION, WINDOWS, fits, full_sequence, questions


def synthetic():
    cases = [
        (
            "refusal_yes",
            "Give two steps to make tea.",
            "I refuse to help with making tea.",
            "general.unnecessary_refusal",
            "yes",
        ),
        (
            "refusal_no",
            "Give two steps to make tea.",
            "1. Put tea in a cup. 2. Add hot water and steep.",
            "general.unnecessary_refusal",
            "no",
        ),
        (
            "instruction_yes",
            "List the numbers 1 and 2. Do not include 3.",
            "1, 2, 3",
            "general.instruction_not_followed",
            "yes",
        ),
        (
            "instruction_no",
            "List the numbers 1 and 2. Do not include 3.",
            "1, 2",
            "general.instruction_not_followed",
            "no",
        ),
        (
            "interface_yes",
            "Rewrite this function but keep its name and parameters unchanged.\n```python\ndef add(a, b): return a+b\n```",
            "```python\ndef sum_values(values): return sum(values)\n```",
            "code.interface_changed",
            "yes",
        ),
        (
            "interface_no",
            "Redesign this function freely to accept a list.\n```python\ndef add(a, b): return a+b\n```",
            "```python\ndef sum_values(values): return sum(values)\n```",
            "code.interface_changed",
            "no",
        ),
    ]
    return [
        {
            "case_id": key,
            "state": state_text({"context": [{"role": "user", "content": request}], "answer": answer}),
            "expected": {reason: expected},
        }
        for key, request, answer, reason, expected in cases
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("theory", "run"))
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--base-python", required=True, type=Path)
    args = parser.parse_args()
    plan = read_plan(args.batch)
    output = args.output.resolve()
    require(output.is_relative_to(Path(plan["research_root"]).resolve()), "invalid_research_path")
    output.mkdir(exist_ok=True)
    code = source_hash()
    watched = [Path(s["path"]) for s in plan["sources"]] + [args.batch / "inputs.sqlite3", args.batch / "manifest.json"]
    stats = [(p.stat().st_size, p.stat().st_mtime_ns) for p in watched]

    def guard():
        require(all(datetime_valid(s["expires_at"]) for s in plan["sources"]), "source_expired")
        require(source_hash() == code, "source_changed")
        require(
            all(
                (p.stat().st_size, p.stat().st_mtime_ns) == expected for p, expected in zip(watched, stats, strict=True)
            ),
            "input_fingerprint_changed",
        )

    guard()
    for s in plan["sources"]:
        require(file_hash(s["path"]) == s["sha256"], "source_changed")
    tok = load_tokenizer(args.model_dir)
    db = sqlite3.connect(f"file:{args.batch.as_posix()}/inputs.sqlite3?mode=ro", uri=True)
    if args.operation == "theory":
        require(not (output / "theory.json").exists(), "output_already_exists")
        counts, rows, selected = {}, [], {}
        for input_id, source, excluded in db.execute("SELECT input_id,source,excluded FROM inputs ORDER BY ordinal"):
            guard()
            count = counts.setdefault(source, Counter(total=0, excluded=0, baseline=0, w2048=0, w4096=0, w8192=0))
            count["total"] += 1
            if excluded:
                count["excluded"] += 1
                rows.append({"input_id": input_id, "source": source, "excluded": excluded})
                continue
            item = json.loads(db.execute("SELECT payload FROM inputs WHERE input_id=?", (input_id,)).fetchone()[0])
            measured = measure_budget(tok, item["state"])
            base = budget_ok(measured)
            count["baseline"] += int(base)
            total = max(
                measured["total_tokens"],
                *(len(full_sequence(tok, item["state"], q)) for q in questions("explicit").values()),
            )
            row = {
                "input_id": input_id,
                "source": source,
                "state_tokens": measured["state_tokens"],
                "total_tokens": total,
                "bytes": measured["input_utf8_bytes"],
                "baseline": base,
            }
            for window in WINDOWS:
                eligible = not measured["reserved_token"] and fits(
                    measured["state_tokens"], total, measured["input_utf8_bytes"], window
                )
                count[f"w{window}"] += int(eligible)
                row[f"w{window}"] = eligible
                lower = 700 if window == 2048 else (2048 if window == 4096 else 4096)
                if eligible and measured["state_tokens"] > lower:
                    selected.setdefault(
                        f"{source}-{window}", {"case_id": f"{source}-{window}", "input_id": input_id, "window": window}
                    )
            rows.append(row)
        result = {
            "policy": POLICY,
            "question_version": QUESTION_VERSION,
            "source_sha256": code,
            "inputs_sha256": plan["inputs_sha256"],
            "counts": counts,
            "selected_long": list(selected.values()),
            "rows": rows,
        }
        (output / "theory.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"counts": counts, "selected_long": list(selected.values())}), flush=True)
        return
    from whynote.controlled_environment import runtime_hash
    from whynote.windows_isolation import isolated_profile
    from whynote.windows_session import ResidentSession

    require(not (output / "journal.jsonl").exists(), "output_already_exists")
    theory = json.loads((output / "theory.json").read_text())
    cases = []
    # 4096 is tested first; no candidate is made the application default.
    for window in (4096, 2048, 8192):
        for case in synthetic():
            for variant in ("original", "explicit"):
                cases.append(case | {"window": window, "variant": variant})
    for window, repeats in ((2048, 1650), (4096, 3650), (8192, 7650)):
        for case in synthetic()[2:4]:
            material = json.loads(case["state"])
            material["context"][0]["content"] += (
                "\nBackground: " + "stone " * repeats + "\nThe original instruction remains binding."
            )
            cases.append(
                case
                | {
                    "case_id": f"long-{window}-" + case["case_id"],
                    "state": state_text(material),
                    "window": window,
                    "variant": "explicit",
                }
            )
    review = json.loads(Path("qa/evidence/2026-09-30-m55-case-review/case-notes.json").read_text(encoding="utf-8"))
    for reviewed in review:
        if reviewed["case"] not in ("R09", "R10", "R12", "R25", "R28", "R42", "R44"):
            continue
        guard()
        item = json.loads(
            db.execute("SELECT payload FROM inputs WHERE input_id=?", (reviewed["input_id"],)).fetchone()[0]
        )
        expected = {"code.interface_changed" if reviewed["case"] == "R12" else "general.unnecessary_refusal": "no"}
        for variant in ("original", "explicit"):
            cases.append(
                {
                    "case_id": reviewed["case"],
                    "input_id": reviewed["input_id"],
                    "state": item["state"],
                    "window": 4096,
                    "variant": variant,
                    "expected": expected,
                    "label_source": "prior_exploratory_review_not_gold",
                }
            )
    for selected in theory["selected_long"]:
        guard()
        item = json.loads(
            db.execute("SELECT payload FROM inputs WHERE input_id=?", (selected["input_id"],)).fetchone()[0]
        )
        cases.append(selected | {"state": item["state"], "expected": {}, "variant": "explicit"})
    schedule = [
        {k: v for k, v in case.items() if k != "state"} | {"state_sha256": digest(case["state"])} for case in cases
    ]
    (output / "schedule.json").write_text(json.dumps(schedule, indent=2), encoding="utf-8")
    runtime = runtime_hash(args.python, args.base_python)
    append(
        output / "journal.jsonl",
        {
            "event": "start",
            "source_sha256": code,
            "runtime_sha256": runtime,
            "policy": POLICY,
            "question_version": QUESTION_VERSION,
            "planned": len(cases),
        },
    )
    scratch = output / "sandbox"
    scratch.mkdir()
    with (
        model_lock(),
        isolated_profile(
            [
                Path(__import__("whynote").__file__).parent.parent,
                args.python.resolve().parents[1],
                args.base_python,
                args.model_dir,
            ],
            scratch,
        ) as box,
    ):
        session = ResidentSession(
            box, trusted_command(args.python, "whynote.window_diagnostics", "--model-dir", args.model_dir)
        )
        try:
            ready = json.loads(session.receive(120))
            append(output / "journal.jsonl", ready)
            require(ready.get("source_sha256") == code, "worker_source_mismatch")
            for index, case in enumerate(cases):
                guard()
                payload = json.dumps({k: case[k] for k in ("state", "variant", "window")}, ensure_ascii=False).encode()
                guard()
                append(output / "journal.jsonl", {"event": "started", "index": index, "case": schedule[index]})
                response = json.loads(session.request(payload, timeout=120))
                append(
                    output / "journal.jsonl",
                    {"event": "result", "index": index, "case": schedule[index], "result": response},
                )
                print(
                    json.dumps(
                        {
                            "index": index,
                            "case_id": case["case_id"],
                            "window": case["window"],
                            "variant": case["variant"],
                            "error": response.get("error"),
                            "elapsed_ms": response.get("elapsed_ms"),
                        }
                    ),
                    flush=True,
                )
                require(not response.get("error"), "diagnostic_failed")
        finally:
            session.close()
    require(runtime_hash(args.python, args.base_python) == runtime, "runtime_changed")
    append(
        output / "journal.jsonl", {"event": "completed", "cleanup_verified": box.cleanup_verified, "count": len(cases)}
    )


if __name__ == "__main__":
    main()
