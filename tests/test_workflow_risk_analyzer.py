from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "エージェント" / "PDCAエージェント" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_risk_analyzer  # noqa: E402


def test_analyze_workflow_refs_extracts_required_and_forbidden(tmp_path: Path) -> None:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(
        "\n".join(
            [
                "# Demo",
                "- 必須: 送信前に確認する",
                "- verify message ack",
                "- 禁止: browser_subagent",
                "- must not skip evidence gate",
            ]
        ),
        encoding="utf-8",
    )

    result = workflow_risk_analyzer.analyze_workflow_refs(
        root=tmp_path,
        workflow_refs=["WORKFLOW.md"],
        latest_errors=["timeout_no_ack"],
    )

    assert result["analyzed_files"]
    assert any("必須" in x or "verify" in x.lower() for x in result["required_signals"])
    assert any("禁止" in x or "must not" in x.lower() for x in result["forbidden_actions"])
    assert result["common_failure_hypotheses"]


def test_analyze_workflow_refs_ignores_missing_files(tmp_path: Path) -> None:
    result = workflow_risk_analyzer.analyze_workflow_refs(
        root=tmp_path,
        workflow_refs=["missing.md"],
        latest_errors=[],
    )
    assert result["analyzed_files"] == []
    assert isinstance(result["verification_hints"], list)
