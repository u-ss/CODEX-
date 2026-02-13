from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _check_script() -> Path:
    return _repo_root() / ".agent" / "workflows" / "check" / "check.py"


def test_check_help_runs() -> None:
    process = subprocess.run([sys.executable, str(_check_script()), "--help"], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0
    assert "usage" in process.stdout.lower()


def test_check_runs_with_no_fail_threshold() -> None:
    process = subprocess.run(
        [sys.executable, str(_check_script()), "--fail-on", "none"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert process.returncode == 0
    assert "report_path" in process.stdout
