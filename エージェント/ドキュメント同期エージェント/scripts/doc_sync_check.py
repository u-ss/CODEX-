#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd, text=True)
    return p.returncode


def main() -> int:
    rc1 = run([sys.executable, "tools/workflow_lint.py"])
    rc2 = run([sys.executable, "tools/repo_hygiene_check.py"])
    return 0 if rc1 == 0 and rc2 == 0 else 1


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("doc_sync", "doc_sync_check", main, phase_name="DOC_SYNC_CHECK_RUN"))

