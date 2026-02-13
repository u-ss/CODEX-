#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SUMMARY_RE = re.compile(
    r"^\[SUMMARY\]\s+errors=(\d+)\s+cautions=(\d+)\s+advisories=(\d+)\s+legacy_warnings=(\d+)\s*$",
    re.MULTILINE,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_output_dir(repo_root: Path) -> Path:
    day = datetime.now().strftime("%Y%m%d")
    out_dir = repo_root / "_outputs" / "workflow_lint" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _run_command(cmd: list[str], timeout: int) -> dict[str, Any]:
    started = datetime.now().isoformat()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "command": cmd,
        "started_at": started,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _parse_summary(text: str) -> dict[str, int] | None:
    m = SUMMARY_RE.search(text or "")
    if not m:
        return None
    return {
        "errors": int(m.group(1)),
        "cautions": int(m.group(2)),
        "advisories": int(m.group(3)),
        "legacy_warnings": int(m.group(4)),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agentized runner for tools/workflow_lint.py (writes artifacts + WorkflowLogger)",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Override output dir (default: _outputs/workflow_lint/<YYYYMMDD>/)",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print full JSON report instead of forwarding lint stdout/stderr",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout (seconds) for the lint subprocess",
    )
    args, forwarded = parser.parse_known_args(argv)

    repo_root = _repo_root()
    tool = repo_root / "tools" / "workflow_lint.py"
    out_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(repo_root)
    report_path = out_dir / "workflow_lint_report.json"

    if not tool.exists():
        payload = {
            "timestamp": datetime.now().isoformat(),
            "repo_root": str(repo_root),
            "status": "SKIP",
            "reason": f"missing: {tool}",
        }
        _write_json(report_path, payload)
        print(json.dumps({"report_path": str(report_path), "status": "SKIP"}, ensure_ascii=False))
        return 0

    cmd = [sys.executable, str(tool), *forwarded]
    result = _run_command(cmd, timeout=int(args.timeout))

    combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    summary = _parse_summary(combined)

    payload: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "repo_root": str(repo_root),
        "tool_path": str(tool),
        "result": result,
        "summary": summary,
    }
    _write_json(report_path, payload)

    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if result.get("stdout"):
            print(result["stdout"], end="")
        if result.get("stderr"):
            print(result["stderr"], end="", file=sys.stderr)
        print(
            json.dumps(
                {"report_path": str(report_path), "returncode": result["returncode"], "summary": summary},
                ensure_ascii=False,
            )
        )

    return int(result["returncode"])


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        raise SystemExit(main())
    raise SystemExit(run_logged_main("workflow_lint", "workflow_lint", main))

