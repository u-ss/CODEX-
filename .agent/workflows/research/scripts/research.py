#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run /research with strict output/log integrity")
    parser.add_argument("query", nargs="*", help="Research query text")
    parser.add_argument("--query", dest="query_opt", default="", help="Research query text")
    parser.add_argument("--goal", default="", help="Goal text (used when query is omitted)")
    parser.add_argument("--focus", default="", help="Focus text (used when query is omitted)")
    parser.add_argument("--task-id", default="", help="Task id for traceability only")
    parser.add_argument("--output-dir", default="", help="Override output directory")
    parser.add_argument("--max-verify-rollbacks", type=int, default=2)
    parser.add_argument(
        "--no-web-tools",
        action="store_true",
        help="Disable local web search/fetch tools and run in stub mode",
    )
    return parser.parse_args()


def _build_query(args: argparse.Namespace) -> str:
    positional = " ".join(args.query).strip()
    if positional:
        return positional
    if args.query_opt.strip():
        return args.query_opt.strip()
    goal = args.goal.strip()
    focus = args.focus.strip()
    if goal and focus:
        return f"{goal} / {focus}"
    if goal:
        return goal
    if focus:
        return focus
    raise ValueError("query is required (positional, --query, or --goal/--focus)")


def main() -> int:
    args = _parse_args()
    root = _repo_root()

    research_root = root / ".agent" / "workflows" / "research"
    shared_root = root / ".agent" / "workflows" / "shared"
    for path in (research_root, shared_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    from lib.orchestrator import OrchestratorConfig, run_research  # type: ignore
    from lib.local_web_tools import LocalWebTools  # type: ignore
    from workflow_logging_hook import run_logged_main  # type: ignore

    def _run() -> int:
        query = _build_query(args)
        output_dir = Path(args.output_dir).resolve() if args.output_dir else None
        tools = None if args.no_web_tools else LocalWebTools()
        config = OrchestratorConfig(
            output_dir=output_dir,
            max_verify_rollbacks=max(0, int(args.max_verify_rollbacks)),
            tools=tools,
        )
        context = run_research(query=query, config=config)
        summary = context.get_summary()
        payload: dict[str, Any] = {
            "session_id": summary.get("session_id"),
            "query": summary.get("query"),
            "output_dir": str(context.output_dir) if context.output_dir else "",
            "task_id": args.task_id,
            "claimed_success": bool(summary.get("has_report")),
            "output_integrity_pass": bool(summary.get("output_integrity_pass")),
            "missing_output_artifacts": summary.get("missing_output_artifacts", []),
            "web_tools_enabled": tools is not None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["claimed_success"] and payload["output_integrity_pass"] else 2

    return run_logged_main("research_cli", "research_entrypoint", _run, phase_name="RESEARCH_ENTRYPOINT")


if __name__ == "__main__":
    raise SystemExit(main())
