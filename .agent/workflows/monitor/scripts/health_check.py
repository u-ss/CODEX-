#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    latency_ms: float
    details: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _try_setup_logger(repo_root: Path):
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from lib.logger import setup_logger, info, warn, error  # type: ignore

        setup_logger(path=repo_root / "_logs" / "monitor.jsonl")
        return info, warn, error
    except Exception as exc:
        import warnings
        warnings.warn(f"[monitor] ロガー初期化失敗: {type(exc).__name__}: {exc}", stacklevel=2)

        def _noop(*args: Any, **kwargs: Any) -> None:
            return

        return _noop, _noop, _noop


def _measure(name: str, fn):
    t0 = time.perf_counter()
    try:
        status, details = fn()
    except Exception as exc:
        status, details = "FAIL", f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return CheckResult(name=name, status=status, latency_ms=round(latency_ms, 2), details=details)


def _check_workflow_scan(repo_root: Path):
    workflows_dir = repo_root / ".agent" / "workflows"
    if not workflows_dir.exists():
        return "FAIL", "workflows directory not found"
    names = sorted(p.name for p in workflows_dir.iterdir() if p.is_dir())
    if not names:
        return "FAIL", "no workflows found"
    return "PASS", f"{len(names)} workflows detected"


def _check_orchestrator_import(repo_root: Path):
    research_root = repo_root / ".agent" / "workflows" / "research"
    orchestrator_path = research_root / "lib" / "orchestrator.py"
    if not orchestrator_path.exists():
        return "FAIL", f"not found: {orchestrator_path}"
    code = (
        "import sys; "
        f"sys.path.insert(0, r'{research_root}'); "
        "from lib import orchestrator as m; "
        "print('ok' if hasattr(m, 'ResearchOrchestrator') else 'missing')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "unknown import error").strip()[:500]
        return "FAIL", details
    marker = (proc.stdout or "").strip()
    if marker == "ok":
        return "PASS", "ResearchOrchestrator import OK"
    return "FAIL", "ResearchOrchestrator symbol not found"


def _check_script_help(script_path: Path):
    if not script_path.exists():
        return "FAIL", f"not found: {script_path}"
    cmd = [sys.executable, str(script_path), "--help"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        return "PASS", "--help OK"
    return "FAIL", (proc.stderr or proc.stdout or "unknown error").strip()[:500]


def _check_pytest(repo_root: Path, run_pytest: bool):
    if not run_pytest:
        return "SKIP", "pytest check disabled (use --run-pytest)"
    target = repo_root / ".agent" / "workflows" / "monitor" / "tests"
    if not target.exists():
        return "SKIP", "monitor tests directory not found"
    cmd = [sys.executable, "-m", "pytest", str(target), "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode == 0:
        return "PASS", "pytest monitor tests passed"
    return "FAIL", (proc.stdout + "\n" + proc.stderr).strip()[-1200:]


def run_health_checks(repo_root: Path, run_pytest: bool) -> dict[str, Any]:
    checks = [
        _measure("workflow_scan", lambda: _check_workflow_scan(repo_root)),
        _measure("research_orchestrator_import", lambda: _check_orchestrator_import(repo_root)),
        _measure(
            "check_agent_help",
            lambda: _check_script_help(repo_root / ".agent" / "workflows" / "check" / "check.py"),
        ),
        _measure(
            "monitor_health_check_help",
            lambda: _check_script_help(repo_root / ".agent" / "workflows" / "monitor" / "scripts" / "health_check.py"),
        ),
        _measure("monitor_pytest_minimal", lambda: _check_pytest(repo_root, run_pytest)),
    ]

    pass_count = sum(1 for c in checks if c.status == "PASS")
    fail_count = sum(1 for c in checks if c.status == "FAIL")
    skip_count = sum(1 for c in checks if c.status == "SKIP")

    report = {
        "timestamp": datetime.now().isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "repo_root": str(repo_root),
        "checks": [asdict(c) for c in checks],
        "summary": {"pass": pass_count, "fail": fail_count, "skip": skip_count},
    }
    return report


def _save_report(repo_root: Path, report: dict[str, Any]) -> Path:
    day = datetime.now().strftime("%Y%m%d")
    out_dir = repo_root / "_outputs" / "monitor" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "health_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor health check")
    parser.add_argument("--run-pytest", action="store_true", help="Run minimal monitor pytest checks")
    parser.add_argument("--print-json", action="store_true", help="Print full report JSON")
    args = parser.parse_args()

    repo_root = _repo_root()
    info, warn, error = _try_setup_logger(repo_root)

    info("monitor_health_start", repo_root=str(repo_root), run_pytest=bool(args.run_pytest))
    report = run_health_checks(repo_root=repo_root, run_pytest=bool(args.run_pytest))
    report_path = _save_report(repo_root=repo_root, report=report)

    summary = report["summary"]
    if summary["fail"] > 0:
        warn("monitor_health_failures", fail=summary["fail"], report_path=str(report_path))
    else:
        info("monitor_health_ok", summary=summary, report_path=str(report_path))

    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "report_path": str(report_path),
                    "summary": summary,
                },
                ensure_ascii=False,
            )
        )
    return 0 if summary["fail"] == 0 else 1


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        raise SystemExit(main())
    raise SystemExit(run_logged_main("monitor", "health_check", main))
