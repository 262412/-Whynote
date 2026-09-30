"""Label-free denominators and a loopback-only, explicit case viewer."""

import hashlib
import html
import json
import secrets
import sqlite3
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .explore_prepare import now, read_plan, save_json
from .explore_run import BUCKETS, append, datetime_valid, rebuild
from .replay_laya import require
from .task_reasons import load_reasons

BUCKET_LABELS = {
    "suggested": "有建议",
    "abstained": "拒识／无匹配",
    "technical_failure": "技术失败",
    "ineligible": "输入超限",
    "skipped": "明确跳过",
    "interrupted": "中断未知",
    "not_started": "未开始",
}


def quantile(db, expression, fraction):
    count = db.execute(f"SELECT COUNT(*) FROM attempts WHERE {expression} IS NOT NULL").fetchone()[0]
    if not count:
        return None
    position = (count - 1) * fraction
    lower = int(position)
    values = [
        r[0]
        for r in db.execute(
            f"SELECT {expression} FROM attempts WHERE {expression} IS NOT NULL ORDER BY {expression} LIMIT 2 OFFSET ?",
            (lower,),
        )
    ]
    return values[0] + (values[-1] - values[0]) * (position - lower)


def report(output):
    output = Path(output)
    plan = read_plan(output)
    db = rebuild(output, "report-index.sqlite3")
    db.execute("ATTACH DATABASE ? AS material", (str(output / "inputs.sqlite3"),))
    counts = Counter({k: 0 for k in BUCKETS})
    counts.update(dict(db.execute("SELECT COALESCE(bucket,'interrupted'),COUNT(*) FROM attempts GROUP BY bucket")))
    counts["not_started"] = plan["planned_slots"] - sum(counts.values())
    require(counts["not_started"] >= 0, "invalid_report_denominator")
    summary = {
        "schema_version": "m55-report-v1",
        "run_id": plan["run_id"],
        "mode": "explore",
        "quality": "NOT_EVALUATED",
        "accuracy": None,
        "reason_coverage": None,
        "routing_omission_truth": None,
        "misleading_suggestion_rate": None,
        "source_records": plan["source_counts"],
        "source_holds": plan["holds"],
        "targets": plan["targets"],
        "conversation_groups": plan["conversation_groups"],
        "shared_context_groups": plan["shared_context_groups"],
        "upstream_near_duplicates": "unresolved",
        "planned_slots": plan["planned_slots"],
        "buckets": dict(counts),
    }
    last_segment, load_count, total_ms = None, 0, 0
    cold, first_metrics, last_metrics, peaks = [], None, None, Counter()
    if (output / "journal.jsonl").exists():
        for line in (output / "journal.jsonl").open(encoding="utf-8"):
            value = json.loads(line)
            if value["event"] in ("segment_stopped", "segment_completed"):
                last_segment = value
                total_ms += value["elapsed_ms"]
            elif value["event"] == "model_loaded":
                load_count += 1
                if len(cold) < 100:
                    cold.append(value["load_ms"])
            if value.get("metrics"):
                last_metrics = value["metrics"]
                first_metrics = first_metrics or last_metrics
                for key, number in last_metrics.items():
                    peaks[key] = max(peaks[key], number or 0)
    summary["status"] = (
        "STOPPED"
        if last_segment and last_segment["status"] == "STOPPED"
        else "PARTIAL"
        if counts["not_started"] or counts["interrupted"]
        else "COMPLETED"
    )
    reasons, errors, tasks = Counter(), Counter(), Counter()
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for (raw,) in db.execute("SELECT result FROM attempts WHERE result IS NOT NULL ORDER BY rowid"):
            row = json.loads(raw)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            errors[row.get("error") or "none"] += 1
            prediction = row.get("prediction") or {}
            tasks[prediction.get("route") or "unknown"] += 1
            reasons.update(prediction.get("reason_ids", []))
    summary["timing"] = {
        "segment_wall_ms": total_ms,
        "model_loads": load_count,
        "cold_load_ms_first_100": cold,
        "attempt_all_outcomes_p50_ms": quantile(db, "elapsed_ms", 0.5),
        "attempt_all_outcomes_p95_ms": quantile(db, "elapsed_ms", 0.95),
        "worker_all_outcomes_p50_ms": quantile(db, "json_extract(result,'$.worker_elapsed_ms')", 0.5),
        "worker_all_outcomes_p95_ms": quantile(db, "json_extract(result,'$.worker_elapsed_ms')", 0.95),
        "finished_slots_per_second": (sum(counts.values()) - counts["not_started"]) * 1000 / total_ms
        if total_ms
        else None,
        "scope": "includes failures/ineligible; startup separate; no quality inference",
    }
    summary["memory"] = {
        "start": first_metrics,
        "end": last_metrics,
        "peak": dict(peaks),
    }
    summary.update(error_codes=dict(errors), inferred_tasks=dict(tasks), suggested_reasons=dict(reasons))
    strata = []
    for source, language, length, targets in db.execute(
        "SELECT source,language,length,COUNT(*) FROM material.inputs GROUP BY source,language,length"
    ):
        buckets = dict(
            db.execute(
                "SELECT COALESCE(a.bucket,'interrupted'),COUNT(*) FROM attempts a JOIN material.inputs i USING(input_id) "
                "WHERE i.source=? AND i.language=? AND i.length=? GROUP BY a.bucket",
                (source, language, length),
            )
        )
        planned = targets * len(plan["schemes"])
        budget_eligible = db.execute(
            "SELECT COUNT(*) FROM attempts a JOIN material.inputs i USING(input_id) "
            "WHERE i.source=? AND i.language=? AND i.length=? "
            "AND json_extract(a.result,'$.measurement.input_utf8_bytes')<=8192 "
            "AND json_extract(a.result,'$.measurement.state_tokens')<=700 "
            "AND json_extract(a.result,'$.measurement.total_tokens')<=1024 "
            "AND json_extract(a.result,'$.measurement.head_tokens')<=256 "
            "AND json_extract(a.result,'$.measurement.max_option_tokens')<=48 "
            "AND json_extract(a.result,'$.measurement.option_total_tokens')<=240 "
            "AND json_extract(a.result,'$.measurement.reserved_token')=0",
            (source, language, length),
        ).fetchone()[0]
        buckets["not_started"] = planned - sum(buckets.values())
        strata.append(
            {
                "source": source,
                "automatic_language": language,
                "length": length,
                "targets": targets,
                "planned_slots": planned,
                "buckets": buckets,
                "runnable": {
                    "numerator": budget_eligible,
                    "denominator": planned,
                    "definition": "measured within pinned budget; unknown measurement never counted eligible",
                },
            }
        )
    summary["strata"] = strata
    examples = defaultdict(list)
    for source, input_id, raw in db.execute(
        "SELECT i.source,a.input_id,a.result FROM attempts a JOIN material.inputs i USING(input_id) WHERE a.result IS NOT NULL"
    ):
        row = json.loads(raw)
        selected = {
            "source": source,
            "input_id": input_id,
            "scheme": row["scheme"],
            "bucket": row["bucket"],
            "error": row.get("error"),
            "task": (row.get("prediction") or {}).get("route"),
            "reasons": (row.get("prediction") or {}).get("reason_ids", []),
        }
        key = hashlib.sha256(f"{plan['seed']}:{input_id}:{row['scheme']}".encode()).hexdigest()
        bucket = examples[row["bucket"]]
        bucket.append((key, selected))
        bucket.sort(key=lambda pair: pair[0])
        if len(bucket) > 50:
            bucket.pop()
    # HTML stays bounded; full metadata remains in JSONL. Stable seed sampling includes every bucket.
    chosen = [row for bucket in examples.values() for _, row in bucket]
    db.close()
    save_json(output / "summary.json", summary)
    body = "<h1>本机无标签诊断</h1><p>质量：NOT_EVALUATED。正确率、真实理由覆盖、路由遗漏与误导率：NA。</p>"
    body += f"<p>运行：{summary['status']} · {plan['targets']} 个目标 · {plan['planned_slots']} 个方案槽 · 模型加载 {load_count} 次</p>"
    body += "<table><tr><th>来源</th><th>扫描记录</th><th>目标</th><th>解析失败</th><th>关联失败</th><th>无目标轮次</th></tr>"
    for source, count in plan["source_counts"].items():
        body += (
            "<tr><td>"
            + html.escape(source)
            + "</td>"
            + "".join(
                f"<td>{count.get(k, 0)}</td>"
                for k in ("scanned", "targets", "parse_failed", "mapping_failed", "no_target")
            )
            + "</tr>"
        )
    body += "</table><p>" + " · ".join(f"{BUCKET_LABELS[k]}：{counts[k]}" for k in BUCKETS) + "</p>"
    body += "<p>此页不含正文。使用 view 入口后按案例按钮在本机查看，查看将登记探索暴露。</p>"
    body += (
        "<details open><summary>汇总与分母</summary><pre>"
        + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
    body += '<label>筛选失败／拒识／超限／任务：<input id="filter" oninput="filterRows()"></label><table><thead><tr><th>来源</th><th>结果</th><th>任务／理由</th><th>引用</th></tr></thead><tbody>'
    reason_labels = {reason.reason_id: reason.label for reason in load_reasons()}
    for row in chosen:
        body += (
            '<tr class="case"><td>'
            + html.escape(row["source"])
            + "</td><td>"
            + html.escape(BUCKET_LABELS[row["bucket"]] + " / " + (row["error"] or ""))
        )
        body += (
            "</td><td>"
            + html.escape(str(row["task"]) + " / " + ", ".join(reason_labels.get(r, r) for r in row["reasons"]))
            + "</td><td>"
        )
        body += f"<button onclick=\"viewCase('{row['input_id']}')\">查看 {row['input_id'][:12]}</button></td></tr>"
    body += "</tbody></table><pre id='case'></pre><script>function filterRows(){let v=document.getElementById('filter').value.toLowerCase();document.querySelectorAll('.case').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(v));}async function viewCase(id){let t=new URLSearchParams(location.search).get('token');if(!t){alert('请先运行 view 命令');return;}let r=await fetch('/case?id='+id+'&token='+encodeURIComponent(t));document.getElementById('case').textContent=await r.text();}</script>"
    page = (
        '<!doctype html><html lang="zh"><meta charset="utf-8"><title>Whynote 本机探索</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:16px}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}button,input{padding:8px}table{width:100%}</style>'
        + body
        + "</html>"
    )
    (output / "report.html").write_text(page, encoding="utf-8")
    return summary


def serve(output, port=0):
    output = Path(output).resolve(strict=True)
    plan = read_plan(output)
    require(all(datetime_valid(s["expires_at"]) for s in plan["sources"]), "source_retention_expired")
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Request URLs contain the local capability; never log them.

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}" or query.get("token") != [token]:
                self.send_error(403)
                return
            if parsed.path == "/":
                data, mime = (output / "report.html").read_bytes(), "text/html; charset=utf-8"
            elif parsed.path == "/case":
                if not all(datetime_valid(s["expires_at"]) for s in plan["sources"]):
                    self.send_error(403)
                    return
                key = query.get("id", [None])[0]
                with sqlite3.connect(f"file:{(output / 'inputs.sqlite3').as_posix()}?mode=ro", uri=True) as db:
                    found = db.execute("SELECT group_id,payload FROM inputs WHERE input_id=?", (key,)).fetchone()
                if found is None:
                    self.send_error(404)
                    return
                append(
                    output / "exposure.jsonl",
                    {
                        "event": "case_view",
                        "input_id": key,
                        "group_id": found[0],
                        "at": now(),
                        "run_id": plan["run_id"],
                    },
                )
                item = json.loads(found[1])
                value = {
                    "input_id": key,
                    "上下文": item["context"],
                    "目标答案": item["answer"],
                    "原反馈与自动注释（不进入模型）": item["reference"],
                }
                index = output / "report-index.sqlite3"
                if index.exists():
                    with sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True) as db:
                        value["模型推测（未获用户确认）"] = [
                            json.loads(r[0])
                            for r in db.execute(
                                "SELECT result FROM attempts WHERE input_id=? AND result IS NOT NULL", (key,)
                            )
                        ]
                data, mime = json.dumps(value, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{server.server_port}/?token={token}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
