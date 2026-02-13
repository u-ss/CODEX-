from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_DIR = ROOT / ".agent" / "workflows" / "shared"
AUTONOMY_DIR = ROOT / "scripts" / "autonomy"

for _path in (SHARED_DIR, AUTONOMY_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import workflow_logger  # noqa: E402
from workflow_logging_hook import run_logged_main  # noqa: E402


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_logged_main_success_creates_verified_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_logger, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setenv("WORKFLOW_LOG_CAPTURE_STREAMS", "0")

    rc = run_logged_main("hook_agent_ok", "hook_flow", lambda: 0, argv=["--demo"])
    assert rc == 0

    latest_path = tmp_path / "_logs" / "autonomy" / "hook_agent_ok" / "latest.json"
    latest = _read_json(latest_path)
    summary = _read_json(tmp_path / latest["summary_path"])

    assert summary["claimed_success"] is True
    assert summary["verified_success"] is True
    assert summary["verification"]["count"] == 1
    assert summary["verification"]["passed"] == 1


def test_run_logged_main_failure_marks_unverified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_logger, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setenv("WORKFLOW_LOG_CAPTURE_STREAMS", "0")

    rc = run_logged_main("hook_agent_ng", "hook_flow", lambda: 2, argv=["--demo"])
    assert rc == 2

    latest_path = tmp_path / "_logs" / "autonomy" / "hook_agent_ng" / "latest.json"
    latest = _read_json(latest_path)
    summary = _read_json(tmp_path / latest["summary_path"])

    assert summary["claimed_success"] is False
    assert summary["verified_success"] is False
    assert summary["verification"]["count"] == 1
    assert summary["verification"]["failed"] == 1
