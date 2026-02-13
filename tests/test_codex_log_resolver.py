from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTONOMY_DIR = ROOT / "scripts" / "autonomy"
if str(AUTONOMY_DIR) not in sys.path:
    sys.path.insert(0, str(AUTONOMY_DIR))

from codex_log_resolver import (  # noqa: E402
    find_research_output_violations,
    find_claim_mismatches,
    list_agents,
    recent_summaries,
    reconcile_research_output,
)
from workflow_logger import WorkflowLogger  # noqa: E402


def _write_run(tmp_path: Path, *, agent: str, claim_success: bool, verification_pass: bool) -> None:
    logger = WorkflowLogger(
        agent=agent,
        workflow="resolver_case",
        workspace_root=tmp_path,
        capture_streams=False,
    )
    verification_id = logger.record_verification(
        checks=[{"name": "gate", "pass": verification_pass}],
        passed=verification_pass,
    )
    if claim_success:
        logger.claim(
            "manual claim",
            evidence_refs=[verification_id],
            claimed_success=True,
        )
    logger.finalize()


def test_resolver_lists_agents_and_recent_summaries(tmp_path: Path) -> None:
    _write_run(tmp_path, agent="resolver_a", claim_success=True, verification_pass=True)
    agents = list_agents(workspace_root=tmp_path)
    names = {agent["agent"] for agent in agents}
    assert "resolver_a" in names

    summaries = recent_summaries(agent="resolver_a", workspace_root=tmp_path)
    assert summaries
    assert summaries[0]["agent"] == "resolver_a"


def test_resolver_detects_claim_vs_verified_mismatch(tmp_path: Path) -> None:
    _write_run(tmp_path, agent="resolver_bad", claim_success=True, verification_pass=False)
    mismatches = find_claim_mismatches(last_n=10, workspace_root=tmp_path)
    assert any(row["agent"] == "resolver_bad" for row in mismatches)


def test_reconcile_research_output_is_idempotent_for_report_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "_outputs" / "research" / "20260210_1456"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "building_code_ai_design_report.md").write_text(
        "# report\n",
        encoding="utf-8",
    )

    first = reconcile_research_output(workspace_root=tmp_path)
    second = reconcile_research_output(workspace_root=tmp_path)

    assert first is not None
    assert second is not None
    assert first["run_id"] == second["run_id"]
    assert first["claimed_success"] is True
    assert first["verified_success"] is False


def test_find_research_output_violations_detects_report_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "_outputs" / "research" / "20260210_1548"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "unity_mcp_ai_game_dev_report.md").write_text("# report\n", encoding="utf-8")

    rows = find_research_output_violations(workspace_root=tmp_path)
    assert rows
    assert rows[0]["output_dir"].endswith("20260210_1548")
    assert "final_report.md" in rows[0]["missing_required"]


def test_find_research_output_violations_accepts_full_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "_outputs" / "research" / "20260210_1600"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_report.md").write_text("# report\n", encoding="utf-8")
    (output_dir / "audit_pack.json").write_text("{}", encoding="utf-8")
    (output_dir / "evidence.jsonl").write_text("{}\n", encoding="utf-8")
    (output_dir / "verified_claims.jsonl").write_text("{}\n", encoding="utf-8")

    rows = find_research_output_violations(workspace_root=tmp_path)
    assert rows == []
