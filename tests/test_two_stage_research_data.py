from whynote import two_stage_inputs as inputs
from whynote import two_stage_research_data as data
from whynote import two_stage_v3 as v3


def test_original_exclusion_without_source_group_is_not_diagnostic():
    flags = data.diagnostic_flags({"excluded": {"result": None}}, {}, {"excluded": None}, seed="fixed")
    assert flags == {"excluded": []}


def test_all_route_size_upper_plan_matches_exact_packets():
    catalog = v3.load_catalog()
    value = inputs.build_input(
        "t", [{"role": "user", "content": "Translate this text:\n" + "word " * 3000}], "翻译" * 300
    )
    forecast = v3.plan_possible_routes(value, catalog)
    maxima = []
    sizes = []
    for task in catalog["tasks"]:
        for domain in catalog["domains"]:
            plan = v3.plan_reasons(value, {"task": {"selected": task}, "domain": {"selected": domain}}, catalog)
            maxima.append(plan["maximum_request_count"])
            sizes.extend(p["record"]["request_utf8_bytes"] for p in plan["packets"])
    assert forecast["maximum_request_count"] == max(maxima)
    assert forecast["largest_sendable_packet_bytes"] == max(sizes)
