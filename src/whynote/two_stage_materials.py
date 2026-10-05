"""Conservative input-only spans. Offsets are Python Unicode codepoints, [start,end).

The caller supplies only its verified pre-answer context. No normalization, model
extraction, code execution, answer/annotation inspection or body persistence.
"""

import hashlib
import re

VERSION = "jev-material-spans-v1"
FIELDS = ("source_text", "original_code", "table", "reference", "tool_trace")
TEXT_TASK = re.compile(r"\b(?:translat\w*|summari[sz]\w*|rewrite|extract)\b|翻译|摘要|总结|概括|改写|抽取", re.I)
CODE_TASK = re.compile(r"\b(?:fix|debug|refactor|rewrite|modify|correct|repair)\b|修改|重构|排错|修复|调试", re.I)
CODE_OBJECT = re.compile(r"\b(?:code|function|script|program|class|implementation)\b|代码|函数|程序", re.I)
LABELS = {
    "source_text": re.compile(
        r"(?m)^[ \t]*(?:source text|original text|text to (?:translate|summari[sz]e|rewrite|extract)|原文|待译文本|待摘要文本)\s*[:：]\s*",
        re.I,
    ),
    "original_code": re.compile(
        r"(?m)^[ \t]*(?:original code|code to (?:fix|debug|refactor|modify)|待修改代码|原始代码|待修复代码)\s*[:：]\s*",
        re.I,
    ),
    "table": re.compile(
        r"(?m)^[ \t]*(?:input (?:data|table)|data (?:table|block)|输入数据|数据表|输入表格)\s*[:：]\s*", re.I
    ),
    "reference": re.compile(
        r"(?m)^[ \t]*(?:verification reference|reference for verification|核验依据|核验参考资料)\s*[:：]\s*", re.I
    ),
}
EXAMPLE = re.compile(
    r"\b(?:example|expected output|sample output|feedback|gold|rating)\b|示例|期望输出|评分|反馈", re.I
)
PRIOR = re.compile(r"\b(?:above|previous|earlier)\b|上文|前文|上述|上一条", re.I)
LINKS = {
    "source_text": re.compile(
        r"\b(?:(?:above|previous|earlier) (?:source )?text|(?:source )?text (?:above|previously supplied))\b|(?:上述|前文|上一条的?)(?:原文|文本)",
        re.I,
    ),
    "original_code": re.compile(
        r"\b(?:(?:above|previous|earlier) code|code above)\b|(?:上述|前文|上一条的?)代码", re.I
    ),
    "table": re.compile(
        r"\b(?:(?:above|previous|earlier) (?:table|data)|(?:table|data) above)\b|(?:上述|前文|上一条的?)(?:表格|数据)",
        re.I,
    ),
}
FENCE = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})[^\r\n]*\r?\n")
CODE_LANGUAGES = {
    "python",
    "py",
    "javascript",
    "js",
    "typescript",
    "ts",
    "sql",
    "java",
    "c",
    "cpp",
    "c++",
    "csharp",
    "c#",
    "go",
    "rust",
    "bash",
    "sh",
}


def blocks(text):
    """Markdown fences close only on a full line with the same, long enough fence."""
    found, cursor = [], 0
    while opening := FENCE.search(text, cursor):
        marker = opening[1]
        closing = re.search(
            r"(?m)^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*\r?$", text[opening.end() :]
        )
        if closing is None:
            return found, True
        end = opening.end() + closing.start()
        found.append((opening.start(), opening.end(), end))
        cursor = opening.end() + closing.end()
    return found, False


def candidates(text):
    fenced, malformed = blocks(text)
    spans = {field: [] for field in FIELDS}
    signaled = set()
    instruction_end = fenced[0][0] if fenced else len(text)
    instruction = text[:instruction_end]
    text_task = bool(TEXT_TASK.search(instruction))
    opening = FENCE.search(text)
    language = opening[0].strip()[len(opening[1]) :].strip().lower() if opening else None
    code_task = bool(CODE_TASK.search(instruction) and (CODE_OBJECT.search(instruction) or language in CODE_LANGUAGES))
    has_material_signal = bool(fenced or malformed or PRIOR.search(text) or re.search(r"[:：\n`\"“]", text))
    if text_task and not code_task and has_material_signal:
        signaled.add("source_text")
    if code_task and has_material_signal:
        signaled.add("original_code")
    for field, pattern in LABELS.items():
        matches = [m for m in pattern.finditer(text) if not any(start <= m.start() < end for start, _, end in fenced)]
        if matches:
            signaled.add(field)
        for match in matches:
            if field == "original_code" and not code_task:
                continue
            if EXAMPLE.search(text[: match.start()]):
                continue
            linked = [span for span in fenced if not text[match.end() : span[0]].strip() and span[0] >= match.end()]
            if linked:
                spans[field].extend((start, end, "labeled_fence") for _, start, end in linked)
            elif not fenced and not malformed and match.end() < len(text):
                # An explicit trailing label defines its entire remaining value.
                spans[field].append((match.end(), len(text), "labeled_tail"))
    if not malformed and len(fenced) == 1:
        _, start, end = fenced[0]
        if not EXAMPLE.search(instruction):
            if code_task and not spans["original_code"]:
                spans["original_code"].append((start, end, "single_code_task_fence"))
            if text_task and not code_task and not spans["source_text"]:
                spans["source_text"].append((start, end, "single_text_task_fence"))
    if not fenced and not malformed and text_task and not code_task and not spans["source_text"]:
        # A task line followed by a colon and newline explicitly bounds a trailing source.
        marker = re.search(r"[:：][ \t]*\r?\n", text)
        if (
            marker
            and TEXT_TASK.search(text[: marker.start()])
            and not EXAMPLE.search(text[: marker.start()])
            and marker.end() < len(text)
        ):
            spans["source_text"].append((marker.end(), len(text), "text_task_tail"))
    # A Markdown table is admitted only when the preceding task names input data/table.
    lines = list(re.finditer(r"[^\r\n]*(?:\r?\n|$)", text))
    for i, line in enumerate(lines[:-1]):
        if "|" not in line[0] or not re.fullmatch(
            r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[i + 1][0]
        ):
            continue
        if any(start <= line.start() < end for start, _, end in fenced):
            continue
        prefix = text[: line.start()]
        signaled.add("table")
        if re.search(r"\b(?:table|data)\b|表格|数据|下表", prefix, re.I) and not EXAMPLE.search(prefix):
            j = i + 2
            while j < len(lines) and "|" in lines[j][0]:
                j += 1
            spans["table"].append((line.start(), lines[j - 1].end(), "input_markdown_table"))
    if re.search(
        r"tool_trace|tool (?:output|record)|test(?:s)? (?:passed|succeeded)|我执行过|测试成功|工具记录", text, re.I
    ):
        signaled.add("tool_trace")
    if PRIOR.search(text) and re.search(r"\b(?:table|data)\b|表格|数据", instruction, re.I):
        signaled.add("table")
    return spans, signaled, malformed


def extract_materials(target_id, context):
    """Extract only the current user request, or uniquely linked earlier user material.

    Generic earlier assistant text cannot establish an independent reference. A
    current task's explicit 'above/previous' reference may link one earlier user
    source/code/table span; multiple candidates remain ambiguous. This snapshot
    has no authenticated tool-record field, so tool_trace is never fabricated.
    """
    index = len(context) - 1
    text = context[index]["content"]
    spans, signaled, malformed = candidates(text)
    materials, metadata = {}, {}
    for field in FIELDS:
        options = [(index, *span) for span in spans[field]]
        bad = malformed and field in signaled and not options
        if not options and field in signaled and field in LINKS and LINKS[field].search(text):
            for earlier, message in enumerate(context[:-1]):
                if message["role"] == "user":
                    prior_spans, prior_signaled, prior_bad = candidates(message["content"])
                    bad |= prior_bad and field in prior_signaled and not prior_spans[field]
                    options.extend((earlier, *span) for span in prior_spans[field])
        options = list({option[:3]: option for option in options}.values())
        status = (
            "parse_failed"
            if bad
            else "ambiguous"
            if len(options) > 1
            else "extracted"
            if options
            else "ambiguous"
            if field in signaled
            else "not_found"
        )
        record = {"status": status, "rule_version": VERSION, "target_id": target_id, "locator": None}
        if status == "extracted":
            message_index, start, end, rule = options[0]
            source = context[message_index]["content"]
            if not source[start:end].strip():
                record["status"] = "parse_failed"
            else:
                materials[field] = source[start:end]
                record["locator"] = {
                    "source_field": "context.content",
                    "message_index": message_index,
                    "start": start,
                    "end": end,
                    "offset_unit": "unicode_codepoint",
                    "interval": "[start,end)",
                    "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    "rule": rule,
                }
        if field == "tool_trace" and status == "ambiguous":
            record["reason_code"] = "no_authenticated_tool_record"
        metadata[field] = record
    return materials, metadata
