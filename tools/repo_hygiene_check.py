#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED_PREFIXES = (
    "_temp/",
    "_screenshots/",
    "_logs/",
    "_outputs/",
    "tmp/",
    ".agent/golden_profile/",
    ".agent/workflows/desktop/logs/",
)
FORBIDDEN_TRACKED_SUBSTRINGS = ("__pycache__/",)
FORBIDDEN_TRACKED_SUFFIXES = (".pyc",)


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    return p.returncode, p.stdout + p.stderr


def tracked_file_violations() -> list[str]:
    rc, out = run(["git", "ls-files"])
    if rc != 0:
        return [f"[ERROR] failed to run git ls-files: {out.strip()}"]

    violations: list[str] = []
    for line in out.splitlines():
        if line == "tmp/.gitkeep":
            continue
        if (
            line.startswith(FORBIDDEN_TRACKED_PREFIXES)
            or any(x in line for x in FORBIDDEN_TRACKED_SUBSTRINGS)
            or line.endswith(FORBIDDEN_TRACKED_SUFFIXES)
        ):
            violations.append(f"[ERROR] forbidden tracked artifact: {line}")
    return violations


def main() -> int:
    findings: list[str] = []

    rc, out = run([sys.executable, str(ROOT / "tools" / "workflow_lint.py")])
    if rc != 0:
        findings.append("[ERROR] workflow lint failed")
        findings.extend([f"  {x}" for x in out.strip().splitlines() if x.strip()])

    findings.extend(tracked_file_violations())

    if findings:
        print("\n".join(findings))
        return 1

    print("[OK] repository hygiene checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
