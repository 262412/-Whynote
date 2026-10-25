"""Local research CLI: review, exact budget plans, threshold search and confirmation."""

import argparse
import json
import sys
from pathlib import Path

from . import two_stage_batch as batch
from . import two_stage_calibration as calibration
from . import two_stage_confirmation as confirmation
from . import two_stage_inputs as inputs
from . import two_stage_research_data as data
from . import two_stage_review as review
from . import two_stage_v3_batch as collection
from .two_stage_review_ui import Workspace, serve


def artifact(directory, prefix, value):
    path = directory / (prefix + "-" + review.fingerprint(value)[:16] + ".json")
    if path.exists():
        review.require(batch.decode(batch.read(path)) == value, "artifact_collision")
    else:
        batch.save(path, value, new=True)
    return str(path)


def prepare(args, project_root):
    root = Path(args.data_root).resolve()
    source = batch.metadata(root)
    batch.source_admission(root, source)
    primary, compare, output = (
        batch.research_path(root, path) for path in (args.source_batch, args.compare_batch, args.output_dir)
    )
    groups = batch.research_path(root, args.groups_file) if args.groups_file else None
    paths = [
        folder / name
        for folder in (primary, compare)
        for name in ("plan.json", "live.results.jsonl", "live.summary.json", "live.sqlite3")
    ]
    for folder in (primary, compare):
        plan = batch.decode(batch.read(folder / "plan.json"))
        paths.extend(Path(plan["runtime"]["project_root"]) / name for name in plan["runtime"]["code_pins"])
    allowed = [
        *batch.offline_files(root, project_root, source, output, None, output / "unused-fake.json"),
        *paths,
        *(project_root / name for name in (*data.MODEL_FILES, *data.ANALYSIS_FILES)),
        project_root / "src/whynote/two_stage_risk.json",
        *(
            output / name
            for name in (
                "sample.manifest.json",
                "size-plan.jsonl",
                "size-summary.json",
                "historical-33588.regression.json",
                "workflow.lock.json",
            )
        ),
    ]
    if groups:
        allowed.append(groups)
    with batch.OfflineGuard(allowed, project_root, write_root=output) as guard:
        result = data.prepare(
            root,
            project_root,
            output,
            primary,
            compare,
            groups,
            seed=args.seed,
            calibration_count=args.calibration_count,
            progress=lambda value: print(json.dumps(value), flush=True),
        )
        review.require(guard.network_denied == 0, "unexpected_network_attempt")
    workspace = Workspace(output, project_root)
    try:
        batch.save(output / "collection.disabled.json", collection.config_template(workspace), new=True)
        batch.save(
            output / "sample-size.plan.json",
            calibration.sample_plan(calibration.risk_config(), available_groups=result["independent_groups"]),
            new=True,
        )
    finally:
        workspace.close()
    return result


def main(argv=None, *, project_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="prepare",
        choices=(
            "prepare",
            "serve",
            "import",
            "sample-size",
            "search",
            "freeze",
            "confirm",
            "supplement",
            "preview",
            "mock",
            "live",
            "report",
        ),
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--workflow")
    parser.add_argument("--source-batch")
    parser.add_argument("--compare-batch")
    parser.add_argument("--groups-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--labels-file")
    parser.add_argument("--prediction-dir")
    parser.add_argument("--search-file")
    parser.add_argument("--supplement-file")
    parser.add_argument("--target-id")
    parser.add_argument("--config-file")
    parser.add_argument("--keys-file")
    parser.add_argument("--batch-id")
    parser.add_argument("--seed", default="jev-review-20261006-v1")
    parser.add_argument("--calibration-count", type=int, default=400)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    root = Path(args.data_root).resolve()
    project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
    workspace = None
    try:
        batch.require(args.mode == "live" or args.keys_file is None, "keys_not_allowed_in_offline_workflow")
        if args.mode == "prepare":
            batch.require(all((args.source_batch, args.compare_batch, args.output_dir)), "prepare_paths_required")
            result = prepare(args, project_root)
        else:
            batch.require(args.workflow is not None, "workflow_required")
            workspace = Workspace(batch.research_path(root, args.workflow), project_root)
            if args.mode == "serve":
                serve(workspace, args.port)
                return 0
            if args.mode == "import":
                batch.require(args.labels_file, "labels_file_required")
                result = workspace.import_labels(batch.decode(batch.read(Path(args.labels_file), 64 * 1024 * 1024)))
            elif args.mode == "sample-size":
                result = calibration.sample_plan(
                    calibration.risk_config(), available_groups=workspace.sample["confirmation_fixed_groups"]
                )
                result = {"plans": result, "artifact": artifact(workspace.path, "sample-size", result)}
            elif args.mode in {"preview", "mock", "live", "report"}:
                config = batch.research_path(root, args.config_file) if args.config_file else None
                result = collection.run(
                    workspace,
                    mode=args.mode,
                    batch_id=args.batch_id or workspace.lock["binding"]["required_prediction_run"],
                    config_path=config,
                    keys_file=args.keys_file,
                )
            elif args.mode == "supplement":
                batch.require(args.target_id and args.supplement_file, "target_and_supplement_file_required")
                _, value = workspace.case(args.target_id)
                payload = batch.decode(batch.read(Path(args.supplement_file)))
                batch.require(
                    {"text", "source"} <= set(payload)
                    and not set(payload) - {"text", "source", "explicit_facts", "explicit_materials"},
                    "explicit_user_supplement_required",
                )
                revised = inputs.supplement(value, **payload)
                # The supplement itself is explicitly supplied local material; its
                # separately versioned artifact retains old input identity.
                result = {
                    "new_evaluation": artifact(workspace.path, "supplement", revised),
                    "input_sha256": revised["input_sha256"],
                    "parent_input_sha256": value["input_sha256"],
                    "requires_new_prediction_and_authorization": True,
                    "route_plan": collection.v3.plan_route(revised, workspace.catalog)[0],
                    "possible_routes": collection.v3.plan_possible_routes(revised, workspace.catalog),
                }
            else:
                registry_path = root / batch.RESEARCH / "jev-confirmation-registry.sqlite3"
                registry = confirmation.ConfirmationRegistry(registry_path)
                try:
                    if args.mode == "search":
                        records, evidence, pins = confirmation.dataset(
                            workspace, args.prediction_dir, partition="calibration"
                        )
                        registry.forbid_search(records)
                        result = calibration.search(
                            records,
                            workspace.catalog,
                            workspace.lock["binding"]["runtime"],
                            workspace.lock["binding"]["required_prediction_run"],
                        )
                        result.update(
                            review_evidence=evidence, data_pins=pins, analysis=workspace.lock["binding"]["analysis"]
                        )
                        path = artifact(workspace.path, "calibration-search", result)
                        figure = Path(path).with_suffix(".svg")
                        if not figure.exists():
                            with figure.open("xb") as stream:
                                stream.write(calibration.curve_svg(result))
                        result = {
                            "status": result["status"],
                            "selected_policy": result["selected_policy"],
                            "artifact": path,
                            "risk_coverage_figure": str(figure),
                            "human_labels": sum(row["label"] is not None for row in records),
                            "matching_predictions": sum(row["prediction"] is not None for row in records),
                        }
                    elif args.mode == "freeze":
                        batch.require(args.search_file, "search_result_required")
                        result = confirmation.freeze(
                            workspace, batch.decode(batch.read(Path(args.search_file), 64 * 1024 * 1024)), registry
                        )
                        batch.save(
                            workspace.path / "confirmation.collection.disabled.json",
                            collection.config_template(workspace, confirmation=result),
                            new=True,
                        )
                    elif args.mode == "confirm":
                        batch.require(args.prediction_dir, "frozen_confirmation_predictions_required")
                        batch.require(
                            (workspace.path / "confirmation.freeze.json").exists(), "freeze_policy_before_confirmation"
                        )
                        records, evidence, pins = confirmation.dataset(
                            workspace, args.prediction_dir, partition="confirmation"
                        )
                        result = confirmation.confirm(workspace, records, registry)
                        result.update(review_evidence=evidence, data_pins=pins)
                        path = artifact(workspace.path, "confirmation-result", result)
                        result = {"status": result["status"], "artifact": path, "production_approved": False}
                    else:
                        raise ValueError("unsupported_research_mode")
                finally:
                    registry.close()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError) and len(str(exc)) < 160 and "\n" not in str(exc)
            else "research_request_or_binding_invalid"
        )
        print(json.dumps({"status": "BLOCKED", "code": code}, ensure_ascii=False), flush=True)
        return 2
    finally:
        if workspace is not None:
            workspace.close()


if __name__ == "__main__":
    sys.exit(main())
