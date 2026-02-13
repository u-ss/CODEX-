from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / ".agent" / "workflows" / "research"
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from lib.orchestrator import OrchestratorConfig, ResearchOrchestrator  # noqa: E402


def test_research_orchestrator_sets_output_integrity_pass(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "1")
    output_dir = tmp_path / "research_ok"
    cfg = OrchestratorConfig(output_dir=output_dir, verbose=False)
    ctx = ResearchOrchestrator(query="integrity smoke", config=cfg).run()
    summary = ctx.get_summary()

    assert summary["has_report"] is True
    assert summary["output_integrity_pass"] is True
    assert summary["missing_output_artifacts"] == []


def test_research_orchestrator_detects_missing_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "1")
    output_dir = tmp_path / "research_ng"
    orchestrator = ResearchOrchestrator(
        query="integrity missing",
        config=OrchestratorConfig(output_dir=output_dir, verbose=False),
    )
    ctx = orchestrator.run()
    (ctx.output_dir / "evidence.jsonl").unlink()
    orchestrator._validate_output_integrity()  # noqa: SLF001 - internal guard behavior test

    assert ctx.output_integrity_pass is False
    assert "evidence.jsonl" in ctx.missing_output_artifacts
