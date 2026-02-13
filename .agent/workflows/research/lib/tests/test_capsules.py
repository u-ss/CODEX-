# -*- coding: utf-8 -*-

from . import __init__  # noqa: F401

import json
from pathlib import Path

from ..capsules import build_capsule, search_capsules


def test_build_capsule_compacts_claim_cards_and_sources():
    audit_pack = {
        "session_id": "s1",
        "query": "q",
        "summary": {"verified_claims_count": 1},
        "report_data": {
            "decisions": [{"claim_id": "c1", "action": "use", "status": "VERIFIED"}],
            "claim_cards": [{
                "claim_id": "c1",
                "statement": "hello",
                "status": "VERIFIED",
                "conditions": [],
                "supporting_evidence": [{"evidence_id": "ev_1", "url": "u", "locator": {"x": 1}, "quote": "a" * 500}],
                "refuting_evidence": [],
            }],
            "sources": ["u"] * 100,
        },
        "output_dir": "_outputs/research/s1",
    }
    cap = build_capsule(audit_pack)
    assert cap["session_id"] == "s1"
    assert cap["sources"] and len(cap["sources"]) <= 50
    assert cap["claim_cards"] and cap["claim_cards"][0]["supporting_evidence"][0]["quote"].endswith("…")


def test_search_capsules_finds_match(tmp_path: Path):
    p = tmp_path / "capsules.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "s1", "query": "OpenAI API pricing", "decisions": [], "claim_cards": []}, ensure_ascii=False),
                json.dumps({"session_id": "s2", "query": "Kubernetes autoscaling", "decisions": [], "claim_cards": []}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    res = search_capsules(query="pricing", limit=5, path=p)
    assert res and res[0]["session_id"] == "s1"
