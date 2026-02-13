#!/usr/bin/env python3
"""WorkflowLoggerログの探索/抽出CLI."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from workflow_logger import WORKSPACE_ROOT, SCHEMA_VERSION, WorkflowLogger


def log_root(workspace_root: Optional[Path] = None) -> Path:
    return (workspace_root or WORKSPACE_ROOT) / "_logs" / "autonomy"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_summary_files(agent_dir: Path) -> list[Path]:
    files: list[Path] = []
    for date_dir in sorted(agent_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        files.extend(sorted(date_dir.glob("*_summary.json"), reverse=True))
    return files


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _latest_research_output_dir(workspace_root: Optional[Path] = None) -> Optional[Path]:
    root = (workspace_root or WORKSPACE_ROOT) / "_outputs" / "research"
    if not root.exists():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _research_report_files(output_dir: Path) -> list[Path]:
    reports = list(output_dir.glob("*_report.md"))
    if reports:
        return sorted(reports, key=lambda p: p.name)
    fallback = output_dir / "final_report.md"
    return [fallback] if fallback.exists() else []


def _research_has_full_artifacts(output_dir: Path) -> bool:
    required = [
        output_dir / "audit_pack.json",
        output_dir / "evidence.jsonl",
        output_dir / "verified_claims.jsonl",
    ]
    return all(path.exists() for path in required)


def find_research_output_violations(workspace_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """_outputs/research のうち report-only / 不完全出力を検出する。"""
    ws = workspace_root or WORKSPACE_ROOT
    root = ws / "_outputs" / "research"
    if not root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for output_dir in sorted([d for d in root.iterdir() if d.is_dir()], key=lambda p: p.name):
        custom_reports = sorted(
            [str(path) for path in output_dir.glob("*_report.md") if path.name != "final_report.md"]
        )
        final_report_path = output_dir / "final_report.md"
        has_final_report = final_report_path.exists()
        if not custom_reports and not has_final_report:
            continue

        required = {
            "final_report.md": has_final_report,
            "audit_pack.json": (output_dir / "audit_pack.json").exists(),
            "evidence.jsonl": (output_dir / "evidence.jsonl").exists(),
            "verified_claims.jsonl": (output_dir / "verified_claims.jsonl").exists(),
        }
        missing = [name for name, present in required.items() if not present]
        if not missing and not custom_reports:
            continue

        rows.append(
            {
                "output_dir": str(output_dir),
                "custom_report_files": custom_reports,
                "has_final_report": has_final_report,
                "missing_required": missing,
            }
        )
    return rows


def _research_summary_for_output_dir(output_dir: Path, workspace_root: Optional[Path] = None) -> Optional[dict[str, Any]]:
    root = log_root(workspace_root)
    agent_dir = root / "research"
    if not agent_dir.exists():
        return None
    output_dir_str = str(output_dir)
    for summary_path in _iter_summary_files(agent_dir):
        try:
            summary = _read_json(summary_path)
        except Exception:
            continue
        inputs = summary.get("inputs", {})
        if str(inputs.get("output_dir", "")) == output_dir_str:
            return summary
        summary_payload = summary.get("outputs", {}).get("summary", {})
        if str(summary_payload.get("session_id", "")) and output_dir.name.endswith(str(summary_payload["session_id"])):
            return summary
    return None


def reconcile_research_output(workspace_root: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """report-onlyの/research出力をautonomyログへ補足取り込みする。"""
    ws = workspace_root or WORKSPACE_ROOT
    output_dir = _latest_research_output_dir(ws)
    if output_dir is None:
        return None

    existing = _research_summary_for_output_dir(output_dir, workspace_root=ws)
    if existing:
        return existing

    report_files = _research_report_files(output_dir)
    if not report_files:
        return None

    logger = WorkflowLogger(
        agent="research",
        workflow="external_output_reconcile",
        workspace_root=ws,
        capture_streams=False,
    )
    logger.set_input("mode", "external_report_only")
    logger.set_input("output_dir", str(output_dir))

    with logger.phase("RECONCILE_OUTPUT") as phase:
        phase.set_output("report_files", [str(path) for path in report_files])
        phase.set_output("output_dir", str(output_dir))

    has_full_artifacts = _research_has_full_artifacts(output_dir)
    checks = [
        {"name": "report_exists", "pass": bool(report_files)},
        {"name": "full_phase_artifacts_present", "pass": has_full_artifacts},
    ]
    verification_id = logger.record_verification(
        checks=checks,
        passed=all(bool(item["pass"]) for item in checks),
        evidence={
            "output_dir": str(output_dir),
            "report_files": [str(path) for path in report_files],
        },
    )
    logger.claim(
        "research_output_detected",
        evidence_refs=[verification_id],
        claimed_success=bool(report_files),
    )
    return logger.finalize()


def list_agents(workspace_root: Optional[Path] = None) -> list[dict[str, Any]]:
    root = log_root(workspace_root)
    if not root.exists():
        return []

    agents: list[dict[str, Any]] = []
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir():
            continue
        latest_path = agent_dir / "latest.json"
        latest = _read_json(latest_path) if latest_path.exists() else {}
        agents.append(
            {
                "agent": agent_dir.name,
                "has_latest": bool(latest),
                "latest_run_id": latest.get("run_id", ""),
                "latest_completed_at": latest.get("completed_at", ""),
                "latest_status": latest.get("final_status", ""),
                "latest_log_path": latest.get("log_path", ""),
                "latest_summary_path": latest.get("summary_path", ""),
            }
        )
    return agents


def latest_for_agent(agent: str, workspace_root: Optional[Path] = None) -> dict[str, Any]:
    root = log_root(workspace_root)
    latest_path = root / agent / "latest.json"
    if not latest_path.exists():
        return {}
    return _read_json(latest_path)


def recent_summaries(agent: str, last_n: int = 3, workspace_root: Optional[Path] = None) -> list[dict[str, Any]]:
    root = log_root(workspace_root)
    agent_dir = root / agent
    if not agent_dir.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for summary_path in _iter_summary_files(agent_dir):
        try:
            data = _read_json(summary_path)
            summaries.append(data)
        except Exception:
            continue
        if len(summaries) >= last_n:
            break
    return summaries


def bundle_for_agent(agent: str, last_n: int = 3, workspace_root: Optional[Path] = None) -> str:
    summaries = recent_summaries(agent=agent, last_n=last_n, workspace_root=workspace_root)
    if not summaries:
        return f"[{agent}] ログサマリーなし"

    lines: list[str] = [f"=== {agent} (latest {len(summaries)} runs) ==="]
    for summary in summaries:
        lines.append(f"- run_id: {summary.get('run_id', '')}")
        lines.append(f"  completed_at: {summary.get('completed_at', '')}")
        lines.append(f"  final_status: {summary.get('final_status', '')}")
        lines.append(
            "  success: "
            f"claimed={summary.get('claimed_success', False)}, "
            f"verified={summary.get('verified_success', False)}"
        )
        lines.append(f"  phases: {summary.get('passed_phases', 0)}/{summary.get('total_phases', 0)}")
        lines.append(f"  log: {summary.get('log_path', '')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def find_claim_mismatches(last_n: int = 20, workspace_root: Optional[Path] = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for agent_info in list_agents(workspace_root):
        agent = agent_info["agent"]
        for summary in recent_summaries(agent=agent, last_n=last_n, workspace_root=workspace_root):
            if summary.get("claimed_success") and not summary.get("verified_success"):
                rows.append(
                    {
                        "agent": agent,
                        "run_id": summary.get("run_id", ""),
                        "completed_at": summary.get("completed_at", ""),
                        "log_path": summary.get("log_path", ""),
                        "summary_path": summary.get("summary_path", ""),
                        "evidence_refs": summary.get("evidence_refs", []),
                    }
                )
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve and bundle WorkflowLogger logs")
    parser.add_argument("--list", action="store_true", help="List agents that have autonomy logs")
    parser.add_argument("--agent", default="", help="Target agent name")
    parser.add_argument("--all", action="store_true", help="Select all agents")
    parser.add_argument("--bundle", action="store_true", help="Print human-readable bundle text")
    parser.add_argument("--last-n", type=int, default=3, help="How many latest runs to include")
    parser.add_argument(
        "--mismatches",
        action="store_true",
        help="List runs where claimed_success=true but verified_success=false",
    )
    parser.add_argument(
        "--reconcile-research-output",
        action="store_true",
        help="(compat) Manually reconcile latest report-only research output into autonomy logs",
    )
    parser.add_argument(
        "--check-research-outputs",
        action="store_true",
        help="Check _outputs/research for bypass-like/incomplete outputs",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = log_root()

    if not root.exists():
        print(json.dumps({"schema_version": SCHEMA_VERSION, "message": "no logs"}, ensure_ascii=False))
        return 0

    if args.reconcile_research_output:
        reconcile_research_output()

    if args.check_research_outputs:
        rows = find_research_output_violations()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if rows else 0

    if args.mismatches:
        print(json.dumps(find_claim_mismatches(last_n=args.last_n), ensure_ascii=False, indent=2))
        return 0

    if args.list:
        print(json.dumps(list_agents(), ensure_ascii=False, indent=2))
        return 0

    if args.agent:
        if args.bundle:
            print(bundle_for_agent(agent=args.agent, last_n=args.last_n))
        else:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "agent": args.agent,
                "latest": latest_for_agent(args.agent),
                "summaries": recent_summaries(agent=args.agent, last_n=args.last_n),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.all:
        agents = [item["agent"] for item in list_agents()]
        if args.bundle:
            chunks = [bundle_for_agent(agent=agent, last_n=args.last_n) for agent in agents]
            print("\n\n".join(chunk for chunk in chunks if chunk))
        else:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "agents": {
                    agent: {
                        "latest": latest_for_agent(agent),
                        "summaries": recent_summaries(agent=agent, last_n=args.last_n),
                    }
                    for agent in agents
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
