"""Source-bound foundation facts and material references for the v3 protocol.

Material bodies remain in one document table. Spans use Unicode codepoints,
not byte offsets. Nothing here calls a model or reads post-answer annotations.
"""

import re
from copy import deepcopy

from . import two_stage_materials as legacy_materials
from .two_stage_review import fingerprint, require, text_hash, validate_span

VERSION = "jev-evidence-input-v4"
FOUNDATION_VERSION = "jev-foundation-v1"
MATERIAL_VERSION = "jev-material-status-v3"
MATERIAL_STATES = {
    "provided_bound",
    "not_provided",
    "extraction_failed",
    "ambiguous_or_conflicting",
    "association_unconfirmed",
    "source_ineligible",
    "omitted_for_budget",
    "not_identified",
}
FACTS = (
    "user_goal",
    "operation",
    "expected_output",
    "explicit_constraints",
    "language",
    "framework",
    "environment",
    "version",
    "target_function",
    "change_mode",
    "expected_behavior",
    "reported_behavior",
    "preserve_interface",
    "preserve_behavior",
    "compatibility_constraints",
    "interface_contract",
    "error_information",
    "test_record",
    "observed_answer_language",
    "observed_answer_framework",
)
PATTERNS = {
    "language": r"\b(Python|JavaScript|TypeScript|Java|C\+\+|C#|Rust|Go|SQL|PHP|Ruby|Kotlin|Swift)\b",
    "framework": r"\b(FastAPI|Django|Flask|React|Vue|Spring|PyTorch|TensorFlow|Node\.js)\b",
    "environment": r"\b(Windows|Linux|macOS|Android|iOS|Docker)\b",
    "version": r"\b(?:Python|FastAPI|Django|Flask|React|Vue|Node(?:\.js)?|Java|CUDA|version)\s*(?:v(?:ersion)?\s*)?\d+(?:\.\d+){0,3}\b|版本\s*\d+(?:\.\d+){0,3}",
    "expected_output": r"(?:expected output|success criteria|output format|预期输出|成功条件|输出格式)\s*[:：][^\r\n]+",
    "expected_behavior": r"(?:expected behavio[u]?r|预期行为)\s*[:：][^\r\n]+",
    "reported_behavior": r"(?:actual behavio[u]?r|observed behavio[u]?r|实际行为|实际输出)\s*[:：][^\r\n]+",
    "interface_contract": r"(?:interface contract|function signature|接口契约|函数签名)\s*[:：][^\r\n]+",
    "error_information": r"(?:error message|traceback|错误信息|报错)\s*[:：][^\r\n]+|(?m:^\s*Error at\b[^\r\n]+)",
    "test_record": r"(?:test record|test output|测试记录|测试输出)\s*[:：][^\r\n]+",
    "preserve_interface": r"(?m)^[^\r\n]*(?:preserv\w*|keep|maintain|unchanged|保持|保留|不[得能]?改)[^\r\n]*(?:interface|signature|接口|签名)[^\r\n]*",
    "preserve_behavior": r"(?m)^[^\r\n]*(?:preserv\w*|keep|maintain|保持|保留|不[得能]?改)[^\r\n]*(?:behavio[u]?r|功能|行为)[^\r\n]*",
    "compatibility_constraints": r"(?m)^[^\r\n]*(?:compatib\w*|兼容)[^\r\n]*",
    "explicit_constraints": r"(?m)^[^\r\n]*(?:\bmust\b|\bonly\b|\bdo not\b|必须|不得|禁止|仅限|只能|格式|JSON)[^\r\n]*",
}


def reference(document, text, start=0, end=None):
    return {"document": document, "start": start, "end": len(text) if end is None else end, "sha256": text_hash(text)}


def _fact(refs=(), *, origin="user_requirement", status=None):
    refs = list(refs)
    return {
        "status": status or ("known" if refs else "unknown"),
        "value": {"spans": refs} if refs else None,
        "origin": origin,
        "evidence": refs,
    }


def fact_text(value, documents):
    if value["status"] not in {"known", "conflict"}:
        return []
    return [documents[r["document"]]["text"][r["start"] : r["end"]] for r in value["evidence"]]


def inline_source_span(text):
    """Recognize explicitly bounded current-user text, without guessing a prior link."""
    if "```" in text or "~~~" in text:
        return None
    patterns = (
        r"\s*(?P<body>.+?)\s*请翻译[。.!]?\s*",
        r"\s*请翻译\s*(?P<body>[A-Za-z].*?)\s*",
        r"\s*(?:请)?翻译(?:成(?:英文|中文|英语|汉语))?\s*[:：]\s*(?P<body>.+?)\s*",
        r"\s*translate[ \t]+(?P<body>[\u3400-\u9fff].*?)\s*",
        r"\s*(?:please\s+)?(?:translate|rewrite|edit|improve|polish|make improvements to)\b"
        r"[^\r\n:：]*\b(?:following|this)\s+(?:[A-Za-z-]+\s+){0,2}"
        r"(?:text|message|paragraph)\b[^\r\n:：]*[:：]\s*(?P<body>.+?)\s*",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text, re.I | re.S)
        if not match:
            continue
        body = match["body"]
        if re.fullmatch(r"(?:上文|前文|上述|这个|这段)(?:的)?(?:文本|原文|文字|内容)?[。.!]?", body):
            return None
        prefix = text[: match.start("body")]
        if legacy_materials.EXAMPLE.search(prefix):
            return None
        return match.span("body")
    return None


def instruction_spans(text):
    """Keep instruction offsets while excluding supplied material, including ambiguity."""
    candidates, _, _ = legacy_materials.candidates(text)
    excluded = [(start, end) for spans in candidates.values() for start, end, _ in spans]
    inline = inline_source_span(text)
    if inline:
        excluded.append(inline)
    fences, malformed = legacy_materials.blocks(text)
    excluded.extend((opening, end) for opening, _, end in fences)
    if malformed:
        # An unclosed fence is data too; it cannot establish a user requirement.
        last_end = max((end for _, _, end in fences), default=0)
        if fences:
            last_end = text.find("\n", last_end) + 1
        opening = legacy_materials.FENCE.search(text, last_end)
        if opening:
            excluded.append((opening.start(), len(text)))
    cursor, result = 0, []
    for start, end in sorted(excluded):
        if start > cursor and text[cursor:start].strip():
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(text) and text[cursor:].strip():
        result.append((cursor, len(text)))
    return result


def build_input(target_id, context, answer, *, explicit_facts=None, explicit_materials=None):
    require(isinstance(context, list) and context and context[-1]["role"] == "user", "request_must_end_context")
    require(isinstance(answer, str) and answer.strip(), "answer_required")
    documents, messages, identities = {}, [], {}

    def add(text, role, source):
        require(isinstance(text, str) and bool(text.strip()), "empty_document")
        key = identities.get((text, role))
        if key is None:
            key = "d" + str(len(documents))
            identities[text, role] = key
            documents[key] = {"text": text, "role": role, "source": source, "sha256": text_hash(text)}
        return key

    for i, message in enumerate(context):
        require(
            set(message) == {"role", "content"} and message["role"] in {"system", "user", "assistant"},
            "invalid_context",
        )
        messages.append(
            {"document": add(message["content"], message["role"], f"context.{i}.content"), "role": message["role"]}
        )
    answer_document = add(answer, "target_answer", "answer")
    request_document = messages[-1]["document"]
    request = context[-1]["content"]
    instruction = instruction_spans(request)
    facts = {name: _fact() for name in FACTS}
    facts["user_goal"] = _fact([reference(request_document, request, start, end) for start, end in instruction])
    facts["target_function"] = deepcopy(facts["user_goal"])
    # Local extraction records literal spans, never a guessed normalized version.
    for field, pattern in PATTERNS.items():
        refs = [
            reference(request_document, request, start + m.start(), start + m.end())
            for start, end in instruction
            for m in re.finditer(pattern, request[start:end], re.I)
        ]
        distinct = {request[r["start"] : r["end"]].casefold() for r in refs}
        conflict = field in {"version", "environment"} and len(distinct) > 1
        facts[field] = _fact(refs, status="conflict" if conflict else None)
        if field in {"reported_behavior", "error_information", "test_record"}:
            facts[field]["origin"] = "user_report"
    for start, end in instruction:
        operation = re.search(
            r"\b(generate|write|create|modify|rewrite|refactor|debug|fix|explain|translate|summari[sz]e)\b|生成|编写|新建|修改|重写|重构|排错|修复|解释|翻译|摘要",
            request[start:end],
            re.I,
        )
        if not operation:
            continue
        facts["operation"] = _fact(
            [reference(request_document, request, start + operation.start(), start + operation.end())]
        )
        if re.fullmatch(
            r"generate|write|create|modify|rewrite|refactor|生成|编写|新建|修改|重写|重构", operation[0], re.I
        ):
            facts["change_mode"] = deepcopy(facts["operation"])
        break
    for field in ("language", "framework"):
        refs = [
            reference(answer_document, answer, m.start(), m.end()) for m in re.finditer(PATTERNS[field], answer, re.I)
        ]
        facts["observed_answer_" + field] = _fact(refs, origin="answer_observation")
    _, historical = legacy_materials.extract_materials(target_id, context)
    materials = {}
    for field, old in historical.items():
        status = {
            "extracted": "provided_bound",
            "parse_failed": "extraction_failed",
            "ambiguous": "ambiguous_or_conflicting",
            "not_found": "not_identified",
        }[old["status"]]
        locator = old["locator"]
        refs = []
        if locator:
            document = messages[locator["message_index"]]["document"]
            refs = [reference(document, documents[document]["text"], locator["start"], locator["end"])]
        if (field == "reference" and old["status"] == "extracted") or (
            field == "tool_trace" and old.get("reason_code") == "no_authenticated_tool_record"
        ):
            status = "source_ineligible"
        # A mention referring outside the admitted prefix does not establish absence.
        if status == "ambiguous_or_conflicting" and legacy_materials.PRIOR.search(request) and len(context) == 1:
            status = "association_unconfirmed"
        materials[field] = {
            "status": status,
            "evidence": refs,
            "origin": "pre_answer_user",
            "reason": old.get("reason_code", old["status"]),
        }
    inline = inline_source_span(request)
    if inline and materials["source_text"]["status"] in {"not_identified", "ambiguous_or_conflicting"}:
        materials["source_text"] = {
            "status": "provided_bound",
            "evidence": [reference(request_document, request, *inline)],
            "origin": "pre_answer_user",
            "reason": "explicit_inline_text_boundary",
        }
    for field, value in (explicit_facts or {}).items():
        require(field in FACTS and set(value) == {"status", "value", "origin", "evidence"}, "invalid_explicit_fact")
        require(value["status"] in {"known", "unknown", "conflict", "inferred"}, "invalid_fact_status")
        require(
            value["origin"]
            in {"user_requirement", "user_report", "answer_observation", "manual_entry", "model_inference"},
            "invalid_fact_origin",
        )
        for span in value["evidence"]:
            validate_span(span, documents)
        require(value["status"] != "known" or bool(value["evidence"]), "known_fact_requires_evidence")
        if value["status"] == "known":
            require(value["value"] == {"spans": value["evidence"]}, "fact_value_must_resolve_to_evidence")
        facts[field] = deepcopy(value)
    for field, value in (explicit_materials or {}).items():
        require(field in materials and set(value) == {"status", "evidence", "origin", "reason"}, "invalid_material")
        require(value["status"] in MATERIAL_STATES, "invalid_material_status")
        for span in value["evidence"]:
            validate_span(span, documents)
            require(documents[span["document"]]["role"] == "user", "material_requires_pre_answer_user_source")
        if value["status"] == "provided_bound":
            require(bool(value["evidence"]), "bound_material_requires_body")
            if field in {"reference", "tool_trace"}:
                require(
                    value["origin"] == ("verified_reference" if field == "reference" else "authenticated_tool_record"),
                    "evidence_source_ineligible",
                )
                require(isinstance(value["reason"], str) and value["reason"].strip(), "verification_reference_required")
        materials[field] = deepcopy(value)
    value = {
        "schema_version": VERSION,
        "foundation_version": FOUNDATION_VERSION,
        "material_version": MATERIAL_VERSION,
        "target_id": target_id,
        "evaluation_revision": 1,
        "parent_input_sha256": None,
        "temporal_scope": "through_target_answer_only",
        "documents": documents,
        "messages": messages,
        "request_document": request_document,
        "answer_document": answer_document,
        "facts": facts,
        "materials": materials,
    }
    value["input_sha256"] = fingerprint(value)
    return value


def evidence_available(value):
    available = {"request", "answer"}
    available.update(name for name, material in value["materials"].items() if material["status"] == "provided_bound")
    for name, fact in value["facts"].items():
        if fact["status"] != "known" or fact["origin"] in {"answer_observation", "model_inference"}:
            continue
        if all(value["documents"][ref["document"]]["role"] == "user" for ref in fact["evidence"]):
            available.add(name)
    return available


def missing_evidence(reason, value):
    available = evidence_available(value)
    paths = reason["evidence_paths"]
    missing = [[key for key in path if key not in available] for path in paths]
    if any(not keys for keys in missing):
        return []
    return min(missing, key=lambda keys: (len(keys), tuple(keys)))


def supplement(value, text, *, source="user_explicit_followup", explicit_facts=None, explicit_materials=None):
    """New user material creates a new evaluation; historical input stays intact."""
    require(source == "user_explicit_followup", "feedback_is_not_prediction_input")
    require(isinstance(text, str) and text.strip(), "supplement_required")
    documents = value["documents"]
    context = [{"role": item["role"], "content": documents[item["document"]]["text"]} for item in value["messages"]]
    context.append({"role": "user", "content": text})
    result = build_input(value["target_id"], context, documents[value["answer_document"]]["text"])
    new_docs = result["documents"]
    mapping = {
        old: next(key for key, doc in new_docs.items() if doc["text"] == item["text"] and doc["role"] == item["role"])
        for old, item in documents.items()
    }

    def moved(record):
        record = deepcopy(record)
        for ref in record["evidence"]:
            ref["document"] = mapping[ref["document"]]
        if "value" in record and record["value"] is not None:
            record["value"] = {"spans": record["evidence"]}
        return record

    for name, prior in value["facts"].items():
        current = result["facts"][name]
        if current["status"] == "unknown" and prior["status"] != "unknown":
            result["facts"][name] = moved(prior)
        elif name in {"user_goal", "target_function"}:
            result["facts"][name] = _fact(moved(prior)["evidence"] + current["evidence"])
        elif name in {"version", "environment"} and current["status"] == prior["status"] == "known":
            if fact_text(prior, documents) != fact_text(current, new_docs):
                result["facts"][name] = _fact(moved(prior)["evidence"] + current["evidence"], status="conflict")
    for name, prior in value["materials"].items():
        current = result["materials"][name]
        if current["status"] == "not_identified" and prior["status"] != "not_identified":
            result["materials"][name] = moved(prior)
        elif (
            current["status"] == prior["status"] == "provided_bound" and current["evidence"] != moved(prior)["evidence"]
        ):
            current.update(status="ambiguous_or_conflicting", evidence=moved(prior)["evidence"] + current["evidence"])
    if explicit_facts or explicit_materials:
        checked = build_input(
            value["target_id"],
            context,
            documents[value["answer_document"]]["text"],
            explicit_facts=explicit_facts,
            explicit_materials=explicit_materials,
        )
        for field in explicit_facts or {}:
            result["facts"][field] = checked["facts"][field]
        for field in explicit_materials or {}:
            result["materials"][field] = checked["materials"][field]
    result.update(
        evaluation_revision=value["evaluation_revision"] + 1,
        parent_input_sha256=value["input_sha256"],
        temporal_scope="user_authorized_followup_new_evaluation",
        original_request_document=mapping[value.get("original_request_document", value["request_document"])],
    )
    result.pop("input_sha256")
    result["input_sha256"] = fingerprint(result)
    return result


def supplementary_questions(missing):
    prompts = {
        "original_code": "请提供与本次修改有关的原始代码或需要保持的接口片段。",
        "interface_contract": "需要保持的函数签名或接口契约是什么？",
        "preserve_interface": "这是新增功能，还是需要保持原接口的修改？",
        "preserve_behavior": "哪些原有行为必须保持？",
        "version": "这次要求适用哪个明确的软件或框架版本？",
        "source_text": "需要对照的原文是哪一段？",
        "reference": "有没有可核验这项结论的资料及其适用版本或日期？",
        "table": "这项分析使用的数据表或相关记录是什么？",
        "tool_trace": "有没有与这次操作对应的实际执行记录？",
        "explicit_constraints": "预期输出、实际输出和必须满足的约束分别是什么？",
    }
    counts = {}
    for item in missing:
        for key in item:
            if key in prompts:
                counts[key] = counts.get(key, 0) + 1
    return [prompts[key] for key in sorted(counts, key=lambda k: (-counts[k], k))[:3]]
