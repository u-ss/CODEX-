from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / ".agent" / "workflows" / "research"
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from lib.context import ResearchRunContext  # noqa: E402
from lib.orchestrator import OrchestratorConfig, ResearchOrchestrator  # noqa: E402
from lib.tool_trace import call_tool  # noqa: E402


class _FakeLogger:
    def __init__(self) -> None:
        self._seq = 0
        self.calls: dict[str, dict] = {}
        self.results: list[dict] = []
        self.verifications: list[dict] = []
        self.claims: list[dict] = []

    def set_input(self, key, value) -> None:
        return

    def set_output(self, key, value) -> None:
        return

    def record_verification(self, checks, passed=None, evidence=None) -> str:
        vid = f"verification_{len(self.verifications) + 1:03d}"
        self.verifications.append(
            {
                "verification_id": vid,
                "checks": checks,
                "passed": passed,
                "evidence": evidence,
            }
        )
        return vid

    def claim(self, message, evidence_refs, claimed_success=True) -> None:
        self.claims.append(
            {
                "message": message,
                "evidence_refs": list(evidence_refs),
                "claimed_success": claimed_success,
            }
        )

    def log_tool_call(self, tool_name, args=None, call_id="") -> str:
        self._seq += 1
        cid = call_id or f"call_{self._seq:04d}"
        self.calls[cid] = {"tool_name": tool_name, "args": args or {}}
        return cid

    def log_tool_result(self, *, call_id, status, result=None, duration_ms=None, error=None) -> None:
        self.results.append(
            {
                "call_id": call_id,
                "status": status,
                "result": result,
                "duration_ms": duration_ms,
                "error": error,
            }
        )


class _FakeWebTools:
    def search_web(self, query: str):  # noqa: ARG002
        return [{"url": "https://example.com/a", "published_at": None}]

    def read_url_content(self, url: str):  # noqa: ARG002
        return "Example content for claim extraction."


def test_call_tool_logs_call_and_result_pairing() -> None:
    logger = _FakeLogger()
    ctx = ResearchRunContext(query="q")
    ctx.workflow_logger = logger

    value = call_tool(
        ctx,
        tool_name="unit.sample",
        call=lambda: [1, 2, 3],
        args={"q": "abc"},
    )

    assert value == [1, 2, 3]
    assert len(logger.calls) == 1
    assert len(logger.results) == 1
    result = logger.results[0]
    assert result["status"] == "ok"
    assert result["call_id"] in logger.calls


def test_call_tool_logs_error_result() -> None:
    logger = _FakeLogger()
    ctx = ResearchRunContext(query="q")
    ctx.workflow_logger = logger

    with pytest.raises(RuntimeError):
        call_tool(
            ctx,
            tool_name="unit.fail",
            call=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    assert len(logger.calls) == 1
    assert len(logger.results) == 1
    assert logger.results[0]["status"] == "error"
    assert logger.results[0]["call_id"] in logger.calls


def test_research_orchestrator_emits_tool_logs_for_phase_saves(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "1")
    logger = _FakeLogger()
    orchestrator = ResearchOrchestrator(
        query="tool logging smoke",
        config=OrchestratorConfig(output_dir=tmp_path / "research_tool", verbose=False),
    )
    ctx = orchestrator._run_impl(wf_logger=logger)  # noqa: SLF001 - integration behavior

    assert ctx.get_summary()["has_report"] is True
    assert logger.calls, "TOOL_CALL が記録されていない"
    assert logger.results, "TOOL_RESULT が記録されていない"
    for result in logger.results:
        assert result["call_id"] in logger.calls

    tool_names = {meta["tool_name"] for meta in logger.calls.values()}
    assert "artifact_writer.save_phase1" in tool_names
    assert "artifact_writer.save_phase4" in tool_names
    assert "artifact_writer.save_audit_pack" in tool_names


def test_research_orchestrator_emits_search_tool_logs_when_tools_wired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "1")
    logger = _FakeLogger()
    orchestrator = ResearchOrchestrator(
        query="tool wiring smoke",
        config=OrchestratorConfig(
            output_dir=tmp_path / "research_wired",
            verbose=False,
            tools=_FakeWebTools(),
        ),
    )
    ctx = orchestrator._run_impl(wf_logger=logger)  # noqa: SLF001

    assert ctx.get_summary()["raw_claims_count"] > 0
    assert ctx.get_summary()["search_queries_attempted"] > 0
    tool_names = [meta["tool_name"] for meta in logger.calls.values()]
    assert "tools.search_web" in tool_names
    assert "tools.read_url_content" in tool_names


def test_research_orchestrator_marks_claim_failed_when_no_search(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "1")
    logger = _FakeLogger()
    orchestrator = ResearchOrchestrator(
        query="must search",
        config=OrchestratorConfig(output_dir=tmp_path / "research_stub", verbose=False, tools=None),
    )
    orchestrator._run_impl(wf_logger=logger)  # noqa: SLF001

    assert logger.verifications
    verification = logger.verifications[-1]
    checks = verification["checks"]
    assert any(c.get("name") == "search_queries_attempted_gt_zero" and c.get("pass") is False for c in checks)
    assert verification["passed"] is False
    assert logger.claims
    assert logger.claims[-1]["claimed_success"] is False
