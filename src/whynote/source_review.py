"""Run synthetic M5 source mapping and review accounting without model or network IO."""

import argparse
import gzip
import hashlib
import json
import re
import zlib
from collections import Counter
from pathlib import Path

from .source_mapping import REVISIONS, MappingError, check_splits, map_row, require

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 1000
CONFIGS = {"wildfb": {"default"}, "helpsteer3": {"feedback"}, "wildfeedback": {"sat_dsat_annotation"}}
SPLITS = {"wildfb": {"train", "test"}, "helpsteer3": {"train", "validation"}, "wildfeedback": {"train"}}


def json_value(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise MappingError("invalid_json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise MappingError("invalid_json") from None


def read_bytes(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "file_too_large")
    return data


def local_path(root, value):
    require(isinstance(value, str) and bool(value), "invalid_path")
    supplied = Path(value)
    require(not supplied.is_absolute() and not supplied.drive, "invalid_path")
    path = (root / supplied).resolve()
    require(path.is_relative_to(root.resolve()), "path_outside_root")
    return path


def checked_file(root, source, prefix):
    expected = source.get(f"{prefix}_sha256")
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid_file_hash")
    data = read_bytes(local_path(root, source.get(f"{prefix}_path")))
    require(hashlib.sha256(data).hexdigest() == expected, "file_hash_mismatch")
    return data


def rows_from(raw, file_format):
    if file_format == "jsonl.gz":
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "file_too_large")
    if file_format == "json":
        rows = json_value(raw)
        require(isinstance(rows, list), "invalid_json_array")
        require(len(rows) <= MAX_ROWS, "too_many_rows")
        return [(i, row, None) for i, row in enumerate(rows)]
    require(file_format in ("jsonl", "jsonl.gz"), "invalid_format")
    lines = raw.splitlines()
    require(len(lines) <= MAX_ROWS, "too_many_rows")
    rows = []
    for index, line in enumerate(lines):
        try:
            rows.append((index, json_value(line), None))
        except MappingError:
            rows.append((index, None, "invalid_json_row"))
    return rows


def run_source(root, source):
    result = {
        "source": source["source"],
        "status": "HOLD",
        "reason": None,
        "rows_read": None,
        "records": [],
        "rejected": [],
    }
    if source.get("status") != "admitted":
        result["reason"] = "source_not_admitted"
        return result
    if source.get("mode") != "synthetic":
        result["reason"] = "public_data_not_enabled"
        return result
    try:
        name = source["source"]
        require(source.get("revision") == REVISIONS[name], "revision_mismatch")
        require(source.get("config") in CONFIGS[name], "config_not_supported")
        require(source.get("split") in SPLITS[name], "invalid_source_split")
        require(source.get("format") in ("json", "jsonl", "jsonl.gz"), "invalid_format")
        reviews = json_value(checked_file(root, source, "reviews"))
        require(isinstance(reviews, dict), "invalid_review_table")
        rows = rows_from(checked_file(root, source, "data"), source["format"])
        result["rows_read"] = len(rows)
        require(bool(rows), "empty_source")
        require(set(reviews) <= {str(i) for i, _, _ in rows}, "unexpected_review_row")
        for row_id, row, error in rows:
            if error:
                result["rejected"].append({"row_id": row_id, "target_key": None, "code": error})
                continue
            records, rejected = map_row(source, row_id, row, reviews.get(str(row_id), {}))
            result["records"].extend(records)
            result["rejected"].extend(rejected)
        result["status"] = "mapped" if result["records"] else "excluded"
        if result["rejected"] and result["records"]:
            result["status"] = "partial"
    except MappingError as exc:
        result["reason"] = str(exc)
    except (OSError, EOFError, zlib.error):
        result["reason"] = "source_io_error"
    except (TypeError, ValueError, RecursionError):
        result["reason"] = "invalid_source_structure"
    return result


def summarize(result):
    records = result["records"]
    count = len(records)
    missing = {}
    for key in (
        "review_ref",
        "screening_ref",
        "group_ref",
        "split_version",
        "task",
        "context_language",
        "feedback_language",
    ):
        number = sum(r["review"][key] in (None, "unknown") for r in records)
        missing[key] = {"count": number, "denominator": count, "rate": number / count if count else None}
    diagnoses, matrix, lengths = Counter(), Counter(), Counter()
    for record in records:
        reviewed = record["review"]["diagnoses"]
        if reviewed is not None:
            diagnoses.update(reviewed)
            matrix["+".join(reviewed) if reviewed else "no_diagnosis"] += 1
        length = record["input_utf8_bytes"]
        lengths["0-1024" if length <= 1024 else "1025-8192" if length <= 8192 else "over-8192"] += 1
    return {
        "source": result["source"],
        "status": result["status"],
        "reason": result["reason"],
        "rows_read": result["rows_read"],
        "mapped_targets": count,
        "rejected_outcomes": len(result["rejected"]),
        "rejection_codes": dict(Counter(r["code"] for r in result["rejected"])),
        "split_conflicts": sum("split_conflict" in r["exclusion_reasons"] for r in records),
        "ready_for_replay": sum(r["ready_for_replay"] for r in records),
        "unreviewed_targets": sum(r["review"]["review_ref"] is None for r in records),
        "missing": missing,
        "diagnoses": dict(diagnoses),
        "diagnosis_matrix": dict(matrix),
        "coverage": {
            key: dict(Counter(r["review"][key] for r in records))
            for key in ("task", "context_language", "feedback_language", "partition")
        },
        "input_utf8_bytes_buckets": dict(lengths),
    }


def run_batch(manifest_path):
    manifest_path = Path(manifest_path)
    manifest_bytes = read_bytes(manifest_path)
    manifest = json_value(manifest_bytes)
    require(isinstance(manifest, dict) and set(manifest) == {"schema_version", "sources"}, "invalid_manifest")
    require(manifest["schema_version"] == "m5-source-batch-v1", "invalid_manifest_version")
    sources = manifest["sources"]
    require(isinstance(sources, list) and 0 < len(sources) <= 3, "invalid_sources")
    names = []
    for source in sources:
        require(isinstance(source, dict) and isinstance(source.get("source"), str), "invalid_source")
        require(source["source"] in REVISIONS and source["source"] not in names, "invalid_source")
        names.append(source["source"])
    results = [run_source(manifest_path.parent, s) for s in sources]
    for name in REVISIONS:
        if name not in names:
            results.append(
                {
                    "source": name,
                    "status": "HOLD",
                    "reason": "source_not_supplied",
                    "rows_read": None,
                    "records": [],
                    "rejected": [],
                }
            )
    records = [r for result in results for r in result["records"]]
    check_splits(records)
    report = {
        "schema_version": "m5-source-report-v1",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "mode": "synthetic",
        "sources": [summarize(r) for r in results],
        "quality_metrics": None,
        "user_cost_metrics": None,
        "near_duplicate_review_complete": False,
    }
    return report, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        # Reserve a new directory before reading source data; never overwrite prior evidence.
        args.output.mkdir(parents=True, exist_ok=False)
        report, results = run_batch(args.manifest)
        for result in results:
            for kind in ("records", "rejected"):
                path = args.output / f"{result['source']}-{kind}.json"
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(result[kind], stream, ensure_ascii=False, indent=2)
        with (args.output / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    except MappingError as exc:
        print(json.dumps({"status": "error", "code": str(exc)}))
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "error", "code": "io_or_manifest_error"}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(s["status"] == "mapped" and not s["split_conflicts"] for s in report["sources"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
