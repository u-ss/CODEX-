# -*- coding: utf-8 -*-

from . import __init__  # noqa: F401

from ..quality_gate import validate_phase35


def test_validate_phase35_requires_counterevidence_log_per_verified_claim():
    errors, warnings = validate_phase35(
        verified_claims=[{"claim_id": "clm_1", "statement": "x", "status": "UNSUPPORTED"}],
        counterevidence_log=[],
        evidence=[],
    )
    assert any("missing counterevidence_log" in e for e in errors)


def test_validate_phase35_citeable_ratio_warning():
    errors, warnings = validate_phase35(
        verified_claims=[{"claim_id": "clm_1", "statement": "x", "status": "UNSUPPORTED"}],
        counterevidence_log=[{"claim_id": "clm_1", "search_queries": ["q1", "q2"], "search_scope": "web", "found_counterevidence": False}],
        evidence=[
            {"quote": "a", "locator": "{\"url\":\"u\",\"heading\":\"\",\"paragraph_index\":0,\"char_start\":0,\"char_end\":1,\"match_type\":\"exact\",\"quote_hash\":\"h\"}"},
            {"quote": "b", "locator": None},
            {"quote": "c", "locator": ""},
        ],
    )
    assert not errors
    assert any("low citeable evidence ratio" in w for w in warnings)


def test_validate_phase35_warns_on_missing_evidence_locator_for_cited_refs():
    errors, warnings = validate_phase35(
        verified_claims=[{
            "claim_id": "clm_1",
            "statement": "x",
            "status": "VERIFIED",
            "supporting_evidence_ids": ["ev_1", "ev_2"],
            "refuting_evidence_ids": [],
        }],
        counterevidence_log=[{"claim_id": "clm_1", "search_queries": ["q1", "q2"], "search_scope": "web", "found_counterevidence": False}],
        evidence=[
            {"evidence_id": "ev_1", "quote": "a", "locator": "{\"url\":\"u\",\"heading\":\"\",\"paragraph_index\":0,\"char_start\":0,\"char_end\":1,\"match_type\":\"exact\",\"quote_hash\":\"h\"}"},
            {"evidence_id": "ev_2", "quote": "b", "locator": None},
        ],
    )
    assert any("missing/weak locator" in e for e in errors)
