"""User-run v3 collection; offline and paid ledgers are strictly separate."""

import asyncio
from collections import Counter
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from . import two_stage_batch as batch
from . import two_stage_research_data as data
from . import two_stage_review as review
from . import two_stage_v3 as v3

CONFIG_VERSION = "jev-v3-collection-config-v1"


def config_template(workspace, *, confirmation=None):
    return {
        "schema_version": CONFIG_VERSION,
        "policy": confirmation["policy"] if confirmation else asdict(v3.Policy()),
        "live": {
            "enabled": False,
            "batch_id": confirmation["primary_run"]
            if confirmation
            else workspace.lock["binding"]["required_prediction_run"],
            "source_plan_sha256": batch.PLAN_SHA256,
            "runtime_sha256": review.fingerprint(workspace.lock["binding"]["runtime"]),
            "expires_at": workspace.lock["binding"]["source_expires_at"],
            "outbound_approval_ref": None,
            "provider_cap_confirmed": False,
            "provider_cap_usd": None,
            "budget": {
                "kind": "new_dedicated",
                "pool_id": None,
                "approval_ref": None,
                "pricing_ref": None,
                "limit_usd": None,
                "reserve_per_stage_usd": None,
                "input_usd_per_million": None,
                "output_usd_per_million": None,
            },
        },
    }


def configuration(path, mode, batch_id, identity):
    config = (
        batch.decode(batch.read(path)) if path else {"schema_version": CONFIG_VERSION, "policy": asdict(v3.Policy())}
    )
    batch.require(
        config.get("schema_version") == CONFIG_VERSION and not set(config) - {"schema_version", "policy", "live"},
        "invalid_v3_config",
    )
    policy = v3.Policy(**config["policy"])
    policy.validate()
    live = config.get("live", {})
    batch.require(
        isinstance(live, dict)
        and not set(live)
        - {
            "enabled",
            "batch_id",
            "source_plan_sha256",
            "runtime_sha256",
            "expires_at",
            "outbound_approval_ref",
            "provider_cap_confirmed",
            "provider_cap_usd",
            "budget",
        },
        "invalid_live_config_fields",
    )
    if "budget" in live:
        batch.require(
            isinstance(live["budget"], dict)
            and not set(live["budget"])
            - {
                "kind",
                "pool_id",
                "approval_ref",
                "pricing_ref",
                "limit_usd",
                "reserve_per_stage_usd",
                "input_usd_per_million",
                "output_usd_per_million",
            },
            "invalid_budget_fields",
        )
    if mode == "live":
        batch.require(
            live.get("enabled") is True and live.get("provider_cap_confirmed") is True, "v3_live_not_authorized"
        )
        batch.require(
            live["batch_id"] == batch_id
            and live["source_plan_sha256"] == batch.PLAN_SHA256
            and live["runtime_sha256"] == review.fingerprint(identity),
            "v3_live_scope_mismatch",
        )
        batch.unexpired(live["expires_at"])
        batch.identifier(live["outbound_approval_ref"])
        budget = live["budget"]
        batch.require(budget.get("kind") == "new_dedicated", "new_dedicated_budget_required")
        for name in ("pool_id", "approval_ref", "pricing_ref"):
            batch.identifier(budget.get(name))
        for name in ("limit_usd", "reserve_per_stage_usd", "input_usd_per_million", "output_usd_per_million"):
            batch.number(budget.get(name), positive=name in {"limit_usd", "reserve_per_stage_usd"})
        batch.require(
            batch.number(live["provider_cap_usd"], positive=True) <= batch.number(budget["limit_usd"]),
            "provider_cap_exceeds_budget",
        )
        batch.require(
            3 * batch.number(budget["reserve_per_stage_usd"]) <= batch.number(budget["limit_usd"]),
            "three_stage_reservation_unavailable",
        )
    return config, policy


class Gate(batch.Gate):
    def __init__(self, root, source, directory, config, config_path, mode, identity, batch_id):
        super().__init__(root, source, directory, config, config_path, "preview")
        self.collection_mode, self.identity, self.batch_id = mode, identity, batch_id

    def check(self):
        super().check()
        if self.collection_mode == "live":
            configuration(self.config_path, "live", self.batch_id, self.identity)


def collection_scope(workspace, batch_id, policy, *, live=False):
    """A confirmation run has its own frozen routing policy and preselected scope."""
    if batch_id == workspace.lock["binding"]["required_prediction_run"]:
        batch.require(asdict(policy) == asdict(v3.Policy()), "primary_collection_policy_changed")
        return {r["target_id"] for r in workspace.sample["rows"] if r["selected"] and r["partition"] == "calibration"}
    frozen_path = workspace.path / "confirmation.freeze.json"
    if frozen_path.exists():
        frozen = batch.decode(batch.read(frozen_path))
        manifest = frozen["manifest"]
        if batch_id == manifest["primary_run"]:
            batch.require(review.fingerprint(manifest) == frozen["id"], "confirmation_freeze_changed")
            batch.require(
                manifest["runtime"] == workspace.lock["binding"]["runtime"]
                and manifest["sample_sha256"] == workspace.sample_sha256,
                "confirmation_binding_changed",
            )
            batch.require(asdict(policy) == manifest["policy"], "confirmation_policy_changed")
            return set(manifest["target_ids"])
    batch.require(not live, "predeclared_prediction_run_required")
    return None


def collect_plan(workspace, batch_id, config, *, target_ids=None):
    plan_rows = [
        batch.decode(line) for line in batch.read(workspace.path / "size-plan.jsonl", 128 * 1024 * 1024).splitlines()
    ]
    targets = []
    rows = {r["target_id"]: r for r in plan_rows}
    for target in workspace.source["targets"]:
        if target_ids is not None and target["target_id"] not in target_ids:
            continue
        row = {k: target[k] for k in ("target_id", "source", "group_id", "original_state_sha256")}
        if not target["eligible"]:
            row.update(
                disposition="original_exclusion",
                exclusion_code=target["exclusion_code"],
                input_sha256=None,
                maximum_requests=0,
            )
        else:
            planned = rows[target["target_id"]]
            row.update(
                input_sha256=planned["input_sha256"],
                group_id=planned["group_id"],
                maximum_requests=planned["maximum_requests"],
                disposition="runnable" if planned["route"]["status"] == "ready" else "input_budget_limited",
                exclusion_code=None,
                route_request_sha256=planned["route"]["request_sha256"],
                route_request_utf8_bytes=planned["route"]["request_utf8_bytes"],
            )
        targets.append(row)
    return {
        "schema_version": "jev-v3-batch-v1",
        "batch_id": batch_id,
        "runtime": workspace.lock["binding"]["runtime"],
        "source_plan_sha256": batch.PLAN_SHA256,
        "sample_sha256": workspace.sample_sha256,
        "config": config,
        "targets": targets,
        "quality_status": "NOT_EVALUATED",
        "input_version": data.inputs.VERSION,
    }


def summary(plan, ledger=None):
    status = (
        Counter(dict(ledger.db.execute("SELECT status,COUNT(*) FROM targets GROUP BY status")))
        if ledger
        else Counter({"not_executed": sum(t["disposition"] == "runnable" for t in plan["targets"])})
    )
    results = (
        [batch.decode(r[0]) for r in ledger.db.execute("SELECT result FROM targets WHERE result IS NOT NULL")]
        if ledger
        else []
    )
    return {
        "schema_version": "jev-v3-summary-v1",
        "batch_id": plan["batch_id"],
        "original_targets": len(plan["targets"]),
        "original_excluded": sum(t["disposition"] == "original_exclusion" for t in plan["targets"]),
        "input_budget_limited_before_route": sum(t["disposition"] == "input_budget_limited" for t in plan["targets"]),
        "statuses": dict(status),
        "assessments": dict(Counter(r["assessment"] for r in results)),
        "maximum_planned_requests": sum(t["maximum_requests"] for t in plan["targets"]),
        "stage_requests_started": dict(
            Counter(
                dict(
                    ledger.db.execute(
                        "SELECT name,COUNT(*) FROM stages WHERE status IN ('started','completed','failed_or_unknown') GROUP BY name"
                    )
                )
            )
        )
        if ledger
        else {},
        "known_usage_estimate": str(ledger.estimate) if ledger else None,
        "unknown_cost_reservations": str(ledger.held) if ledger else None,
        "actual_charge_usd": None,
        "quality_status": "NOT_EVALUATED",
        "thresholds_calibrated": False,
        "production_approved": False,
    }


def ledger_records(plan, ledger):
    targets = {r["id"]: r for r in ledger.db.execute("SELECT * FROM targets")}
    rows = []
    for target in plan["targets"]:
        row = targets.get(target["target_id"])
        stages = [
            batch.decode(r["metadata"])
            | {
                "stage": r["name"],
                "status": r["status"],
                "held_reservation": r["held"],
                "known_usage_estimate": r["estimate"],
            }
            for r in ledger.db.execute("SELECT * FROM stages WHERE target=? ORDER BY name DESC", (target["target_id"],))
        ]
        rows.append(
            target
            | {
                "schema_version": "jev-v3-record-v1",
                "batch_id": plan["batch_id"],
                "mode": ledger.mode,
                "synthetic": ledger.mode == "mock",
                "technical_status": row["status"] if row else target["disposition"],
                "error_code": row["error"] if row else None,
                "result": batch.decode(row["result"]) if row and row["result"] else None,
                "stages": stages,
            }
        )
    return rows


def export(directory, plan, ledger):
    rows = ledger_records(plan, ledger)
    temporary = directory / f"{ledger.mode}.results.tmp"
    data.offline.write_lines(temporary, rows)
    temporary.replace(directory / f"{ledger.mode}.results.jsonl")
    result = summary(plan, ledger)
    batch.save(directory / f"{ledger.mode}.summary.json", result)
    return result


async def execute(workspace, plan, ledger, gate, reader, keys_file, policy, transport):
    originals = {t["target_id"]: t for t in workspace.source["targets"]}
    pending = ledger.pending()
    for target in plan["targets"]:
        key = target["target_id"]
        if key not in pending:
            continue
        gate.check()
        refs = ledger.claim(target, stages=v3.STAGES[: target["maximum_requests"]])
        captured, observed = {}, {}

        def state_factory(key=key, target=target, captured=captured):
            value, _, _ = data.original_input(reader, originals[key])
            batch.require(value["input_sha256"] == target["input_sha256"], "collection_input_changed")
            captured["input"] = value
            return value

        def callback(event, record, key=key, observed=observed):
            if event == "started":
                gate.check()
            ledger.stage(key, event, record)
            observed[record["stage"]] = record
            if event == "started":
                gate.check()

        try:
            result = await v3.classify(
                state_factory,
                keys_file=keys_file,
                policy=policy,
                enabled=True,
                outbound_approval_ref=plan["config"].get("live", {}).get("outbound_approval_ref") or "synthetic_only",
                budget_reservations=refs,
                admission_check=gate.allowed,
                transport=transport,
                stage_callback=callback,
            )
            ledger.finish(key, result["technical_status"], result=result)
        except v3.legacy.StageError as exc:
            partial = v3.partial_result(
                captured.get("input"), policy, list(observed.values()), technical_status="partial_error"
            )
            ledger.finish(key, "partial_error" if partial else "error", str(exc), result=partial)
            if str(exc) == "admission_revoked":
                raise batch.Closed(gate.reason or "admission_revoked") from None
        except (batch.Closed, v3.NotFoundError):
            partial = v3.partial_result(
                captured.get("input"), policy, list(observed.values()), technical_status="reconcile"
            )
            ledger.finish(key, "reconcile", "admission_or_budget_closed", result=partial)
            raise
        except ValueError:
            ledger.finish(key, "error", "local_v3_validation_failed")


def run(workspace, *, mode, batch_id, config_path=None, keys_file=None):
    batch.identifier(batch_id)
    batch.require(mode in {"preview", "mock", "live", "report"}, "invalid_collection_mode")
    batch.require(mode == "live" or keys_file is None, "keys_only_allowed_for_user_live")
    workspace.check()
    identity = workspace.lock["binding"]["runtime"]
    directory = workspace.root / batch.RESEARCH / ("m55-two-stage-v3-" + ("mock-" if mode == "mock" else "") + batch_id)
    config, policy = configuration(config_path, mode, batch_id, identity)
    if mode == "report" and config_path is None:
        config = batch.decode(batch.read(directory / "plan.json"))["config"]
        policy = v3.Policy(**config["policy"])
    target_ids = collection_scope(workspace, batch_id, policy, live=mode == "live")
    if mode == "live":
        batch.require(
            keys_file is not None and Path(keys_file).is_absolute(), "explicit_key_path_required_for_user_live"
        )
    gate = Gate(workspace.root, workspace.source, directory, config, config_path, mode, identity, batch_id)
    gate.check()
    plan = collect_plan(workspace, batch_id, config, target_ids=target_ids)
    if mode == "preview":
        result = summary(plan) | {"live_enabled": config.get("live", {}).get("enabled") is True}
        path = workspace.path / ("collection.preview-" + review.fingerprint(plan)[:16] + ".json")
        if not path.exists():
            batch.save(path, {"plan": plan, "summary": result}, new=True)
        return result | {"artifact": str(path), "live_batch_created": False}
    if mode == "report":
        batch.require(directory.exists(), "live_batch_missing")
    directory.mkdir(exist_ok=True)
    with (
        batch.batch_lock(directory / ".run.lock"),
        (
            batch.batch_lock(workspace.root / batch.RESEARCH / ".two-stage-budget.lock")
            if mode == "live"
            else nullcontext()
        ),
    ):
        path = directory / "plan.json"
        if path.exists():
            batch.require(batch.decode(batch.read(path)) == plan, "v3_batch_binding_changed_new_batch_required")
        else:
            batch.require(not list(directory.glob("*.sqlite3")), "missing_plan_reconcile_required")
            batch.save(path, plan, new=True)
        if mode == "report":
            path = directory / "live.sqlite3"
            batch.require(path.exists(), "live_ledger_missing")
            ledger = batch.Ledger(path, plan, "live", readonly=True)
            try:
                return export(directory, plan, ledger)
            finally:
                ledger.close()
        if mode == "live":
            batch.register_pool(workspace.root, directory, plan, create=False)
            batch.register_pool(workspace.root, directory, plan)
        ledger = batch.Ledger(directory / (mode + ".sqlite3"), plan, mode)
        gate.ledger = ledger
        try:
            ledger.recover()
            with TemporaryDirectory(prefix="whynote-v3-fake-") as temporary:
                fake = Path(temporary) / "fake.json"
                transport = None
                if mode == "mock":
                    import httpx

                    fake.write_text('{"typesafe":"synthetic-key-only"}', encoding="utf-8")
                    keys_file = fake
                    transport = httpx.MockTransport(batch.synthetic_response)
                allowed = [
                    *batch.offline_files(
                        workspace.root, workspace.project_root, workspace.source, directory, config_path, fake
                    ),
                    *(workspace.project_root / name for name in (*data.MODEL_FILES, *data.ANALYSIS_FILES)),
                    workspace.path / "workflow.lock.json",
                    *(workspace.path / name for name in workspace.lock["files"]),
                ]
                # The event loop creates local sockets before the network-denying
                # audit hook. Mock can then read only pinned inputs and a fake key.
                with asyncio.Runner() as runner:
                    runner.get_loop()
                    guard = (
                        batch.OfflineGuard(allowed, workspace.project_root, write_root=directory)
                        if mode == "mock"
                        else nullcontext()
                    )
                    with guard, batch.Inputs(workspace.root, workspace.source, gate) as reader:
                        try:
                            runner.run(execute(workspace, plan, ledger, gate, reader, keys_file, policy, transport))
                        finally:
                            result = export(directory, plan, ledger)
                    if mode == "mock":
                        batch.require(guard.network_denied == 0, "unexpected_mock_network_attempt")
            return result
        finally:
            ledger.close()
