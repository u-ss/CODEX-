#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTONOMY_DIR = ROOT / "scripts" / "autonomy"
if str(AUTONOMY_DIR) not in sys.path:
    sys.path.insert(0, str(AUTONOMY_DIR))

from workflow_logger import WorkflowLogger  # noqa: E402


def main() -> int:
    logger = WorkflowLogger(agent="sample_agent", workflow="logging_sample", capture_streams=False)
    with logger.phase("PLAN") as phase:
        phase.set_input("goal", "logging sample run")
        phase.set_output("task_count", 2)

    call_id = logger.log_tool_call("echo", args={"text": "hello"})
    logger.log_tool_result(call_id=call_id, status="ok", result={"stdout": "hello"}, duration_ms=3)

    verification_id = logger.record_verification(
        checks=[
            {"name": "log_file_exists", "pass": True},
            {"name": "summary_file_exists", "pass": True},
        ],
        passed=True,
    )
    logger.claim("sample run completed", evidence_refs=[verification_id], claimed_success=True)
    summary = logger.finalize()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

