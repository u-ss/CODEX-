from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTONOMY_DIR = ROOT / "scripts" / "autonomy"
if str(AUTONOMY_DIR) not in sys.path:
    sys.path.insert(0, str(AUTONOMY_DIR))

from workflow_logger import WorkflowLogger  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_workflow_logger_writes_run_logs_and_summary(tmp_path: Path) -> None:
    logger = WorkflowLogger(
        agent="unit_agent",
        workflow="unit_workflow",
        workspace_root=tmp_path,
        capture_streams=False,
    )
    with logger.phase("TEST_PHASE") as phase:
        phase.set_input("x", 1)
        phase.set_output("y", 2)
        phase.add_metric("m", 3)

    verification_id = logger.record_verification(
        checks=[{"name": "unit_check", "pass": True}],
        passed=True,
    )
    logger.claim("unit done", evidence_refs=[verification_id], claimed_success=True)
    summary = logger.finalize()

    log_path = tmp_path / summary["log_path"]
    summary_path = tmp_path / summary["summary_path"]
    latest_path = tmp_path / "_logs" / "autonomy" / "unit_agent" / "latest.json"

    assert log_path.exists()
    assert summary_path.exists()
    assert latest_path.exists()
    assert summary["claimed_success"] is True
    assert summary["verified_success"] is True

    events = _read_jsonl(log_path)
    assert events
    assert events[0]["event_type"] == "TASK_RECEIVED"
    assert any(event["event_type"] == "RUN_SUMMARY" for event in events)
    assert [event["event_seq"] for event in events] == sorted(event["event_seq"] for event in events)

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest["run_id"] == summary["run_id"]


def test_workflow_logger_supports_tool_call_linkage(tmp_path: Path) -> None:
    logger = WorkflowLogger(
        agent="tool_agent",
        workflow="tool_flow",
        workspace_root=tmp_path,
        capture_streams=False,
    )
    call_id = logger.log_tool_call("unit_tool", args={"q": "abc"})
    logger.log_tool_result(call_id=call_id, status="ok", result={"value": 1}, duration_ms=12)
    summary = logger.finalize()

    log_path = tmp_path / summary["log_path"]
    events = _read_jsonl(log_path)
    call_events = [e for e in events if e["event_type"] == "TOOL_CALL"]
    result_events = [e for e in events if e["event_type"] == "TOOL_RESULT"]

    assert call_events and result_events
    assert call_events[0]["payload"]["call_id"] == call_id
    assert result_events[0]["payload"]["call_id"] == call_id


def test_workflow_logger_does_not_redact_verification_pass_flag(tmp_path: Path) -> None:
    logger = WorkflowLogger(
        agent="verify_agent",
        workflow="verify_flow",
        workspace_root=tmp_path,
        capture_streams=False,
    )
    logger.record_verification(
        checks=[{"name": "exit_code_zero", "pass": True}],
        passed=True,
        evidence={"password": "p@ssw0rd", "note": "ok"},
    )
    summary = logger.finalize()

    log_path = tmp_path / summary["log_path"]
    events = _read_jsonl(log_path)
    verification_event = next(e for e in events if e["event_type"] == "VERIFICATION_RUN")
    checks = verification_event["payload"]["checks"]
    evidence = verification_event["payload"]["evidence"]

    assert checks[0]["pass"] is True
    assert evidence["password"] == "***REDACTED***"
    assert evidence["note"] == "ok"
