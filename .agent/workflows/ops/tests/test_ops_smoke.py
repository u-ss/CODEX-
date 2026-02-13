from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _ops_script() -> Path:
    return _repo_root() / ".agent" / "workflows" / "ops" / "scripts" / "ops.py"


def test_ops_help_runs() -> None:
    proc = subprocess.run([sys.executable, str(_ops_script()), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "subcommands" in proc.stdout.lower() or "usage" in proc.stdout.lower()


def test_ops_health_runs() -> None:
    proc = subprocess.run(
        [sys.executable, str(_ops_script()), "health"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0
    assert "report_path" in proc.stdout
