"""D-21 prepare/run/resume/report/view. No labels or quality-pass gate in explore."""

import argparse
import json
from pathlib import Path

from .explore_prepare import prepare
from .explore_report import report, serve
from .explore_run import real_run
from .replay_laya import ReplayError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("explore",), default="explore")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--records", default="100", help="source records per source, or explicit all")
    p.add_argument("--targets", type=int)
    p.add_argument("--schemes", nargs="+", choices=("A", "B", "C"), default=["C"])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--holdout-percent", type=int, default=0)
    for name in ("run", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--output", required=True, type=Path)
        for key in ("python", "base-python", "model-dir"):
            p.add_argument("--" + key, required=True)
        p.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
        p.add_argument("--load-timeout", type=float, default=60)
        p.add_argument("--request-timeout", type=float, default=60)
    for name in ("report", "view"):
        p = sub.add_parser(name)
        p.add_argument("--output", required=True, type=Path)
        if name == "view":
            p.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            value = prepare(
                json.loads(args.manifest.read_text(encoding="utf-8")),
                args.output,
                records=None if args.records == "all" else int(args.records),
                targets=args.targets,
                schemes=args.schemes,
                seed=args.seed,
                holdout_percent=args.holdout_percent,
            )
            print(json.dumps({k: value[k] for k in ("run_id", "targets", "planned_slots", "source_counts")}))
        elif args.command in ("run", "resume"):
            result = real_run(
                args.output,
                {
                    key: getattr(args, key)
                    for key in ("python", "base_python", "model_dir", "device", "load_timeout", "request_timeout")
                },
                resume=args.command == "resume",
            )
            report(args.output)
            print(json.dumps(result))
            return 2 if result["status"] == "STOPPED" else 0
        elif args.command == "report":
            value = report(args.output)
            print(json.dumps({k: value[k] for k in ("status", "quality", "planned_slots", "buckets")}))
        else:
            serve(args.output, args.port)
    except ReplayError as exc:
        print(json.dumps({"status": "STOPPED", "error": str(exc)}))
        return 2
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "STOPPED", "error": "invalid_explore_input"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
