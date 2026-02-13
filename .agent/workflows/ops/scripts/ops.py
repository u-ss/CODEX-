#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _outputs_dir(repo_root: Path) -> Path:
    day = datetime.now().strftime("%Y%m%d")
    out_dir = repo_root / "_outputs" / "ops" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _try_setup_logger(repo_root: Path):
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from lib.logger import setup_logger, info, warn, error  # type: ignore

        setup_logger(path=repo_root / "_logs" / "ops.jsonl")
        return info, warn, error
    except Exception:
        def _noop(*args: Any, **kwargs: Any) -> None:
            return

        return _noop, _noop, _noop


def _run_command(cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    started = datetime.now().isoformat()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "command": cmd,
        "started_at": started,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_health(repo_root: Path, out_dir: Path) -> int:
    script = repo_root / ".agent" / "workflows" / "monitor" / "scripts" / "health_check.py"
    result = _run_command([sys.executable, str(script)], timeout=120)
    _write_json(out_dir / "health_result.json", result)
    if result["returncode"] != 0:
        print(result["stderr"] or result["stdout"], end="")
        return result["returncode"]
    print(result["stdout"], end="")
    return 0


def _run_if_exists(repo_root: Path, cmd: list[str], relative_path: str, timeout: int = 180) -> dict[str, Any]:
    target = repo_root / relative_path
    if not target.exists():
        return {
            "command": cmd,
            "status": "SKIP",
            "reason": f"missing: {target}",
        }
    result = _run_command(cmd, timeout=timeout)
    result["status"] = "PASS" if result["returncode"] == 0 else "FAIL"
    return result


def cmd_deploy(repo_root: Path, out_dir: Path, dry_run: bool) -> int:
    checks: list[dict[str, Any]] = []
    status = _run_command(["git", "status", "--short"], timeout=60)
    is_dirty = bool((status.get("stdout") or "").strip() or (status.get("stderr") or "").strip())
    status["status"] = "PASS" if status["returncode"] == 0 and not is_dirty else "FAIL"
    checks.append(status)
    checks.append(
        _run_if_exists(
            repo_root=repo_root,
            cmd=[sys.executable, str(repo_root / "tools" / "workflow_lint.py")],
            relative_path="tools/workflow_lint.py",
            timeout=180,
        )
    )
    checks.append(
        _run_if_exists(
            repo_root=repo_root,
            cmd=[sys.executable, "-m", "pytest", str(repo_root / ".agent" / "workflows" / "code" / "lib" / "tests"), "-q"],
            relative_path=".agent/workflows/code/lib/tests",
            timeout=240,
        )
    )

    report = {
        "subcommand": "deploy",
        "dry_run": dry_run,
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
    }
    _write_json(out_dir / "deploy_dry_run.json", report)
    print(json.dumps({"artifact": str(out_dir / "deploy_dry_run.json"), "checks": len(checks)}, ensure_ascii=False))
    failed = [c for c in checks if c.get("status") == "FAIL"]
    return 0 if not failed else 1


def cmd_clean(repo_root: Path, out_dir: Path, dry_run: bool) -> int:
    script = repo_root / "tools" / "clean_runtime_artifacts.ps1"
    if not script.exists():
        payload = {"status": "SKIP", "reason": f"missing: {script}"}
        _write_json(out_dir / "clean_result.json", payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if dry_run:
        cmd.append("-DryRun")
    result = _run_command(cmd, timeout=240)
    _write_json(out_dir / "clean_result.json", result)
    print(result["stdout"], end="")
    return 0 if result["returncode"] == 0 else result["returncode"]


def cmd_doc_sync(
    repo_root: Path,
    out_dir: Path,
    check: bool,
    agent_map: bool = False,
    workspace_scan: bool = False,
) -> int:
    if not any([check, agent_map, workspace_scan]):
        payload = {
            "status": "SKIP",
            "reason": "doc-sync requires --check and/or --agent-map and/or --workspace-scan",
        }
        _write_json(out_dir / "doc_sync_result.json", payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    checks: list[dict[str, Any]] = []
    analysis_runs: list[dict[str, Any]] = []
    if check:
        checks.append(
            _run_if_exists(
                repo_root=repo_root,
                cmd=[sys.executable, str(repo_root / "tools" / "workflow_lint.py")],
                relative_path="tools/workflow_lint.py",
                timeout=180,
            )
        )
        checks.append(
            _run_if_exists(
                repo_root=repo_root,
                cmd=[sys.executable, str(repo_root / "tools" / "repo_hygiene_check.py")],
                relative_path="tools/repo_hygiene_check.py",
                timeout=180,
            )
        )

    analyzer_script = repo_root / "エージェント" / "フォルダ解析エージェント" / "scripts" / "folder_analyzer.py"
    analysis_output_dir = out_dir / "folder_check"
    if agent_map:
        if analyzer_script.exists():
            result = _run_command(
                [
                    sys.executable,
                    str(analyzer_script),
                    "--agent-map",
                    "--output-dir",
                    str(analysis_output_dir),
                ],
                timeout=240,
            )
            result["status"] = "PASS" if result["returncode"] == 0 else "FAIL"
            analysis_runs.append(result)
        else:
            analysis_runs.append(
                {
                    "command": ["folder_analyzer.py", "--agent-map"],
                    "status": "SKIP",
                    "reason": f"missing: {analyzer_script}",
                }
            )

    if workspace_scan:
        if analyzer_script.exists():
            result = _run_command(
                [
                    sys.executable,
                    str(analyzer_script),
                    "--workspace",
                    "--output-dir",
                    str(analysis_output_dir),
                ],
                timeout=600,
            )
            result["status"] = "PASS" if result["returncode"] == 0 else "FAIL"
            analysis_runs.append(result)
        else:
            analysis_runs.append(
                {
                    "command": ["folder_analyzer.py", "--workspace"],
                    "status": "SKIP",
                    "reason": f"missing: {analyzer_script}",
                }
            )

    payload = {
        "checks": checks,
        "analysis_runs": analysis_runs,
        "timestamp": datetime.now().isoformat(),
    }
    _write_json(out_dir / "doc_sync_result.json", payload)
    print(
        json.dumps(
            {
                "artifact": str(out_dir / "doc_sync_result.json"),
                "checks": len(checks),
                "analysis_runs": len(analysis_runs),
            },
            ensure_ascii=False,
        )
    )
    failed = [c for c in checks if c.get("status") == "FAIL"]
    failed += [c for c in analysis_runs if c.get("status") == "FAIL"]
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified operations workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Run monitor health checks")

    deploy = sub.add_parser("deploy", help="Run deploy dry-run checks")
    deploy.add_argument("--dry-run", action="store_true", dest="dry_run", default=True, help="Use dry-run mode (default)")
    deploy.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="Disable dry-run (not recommended)")

    clean = sub.add_parser("clean", help="Run clean_runtime_artifacts")
    clean.add_argument("--dry-run", action="store_true", dest="dry_run", default=True, help="Use dry-run mode (default)")
    clean.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="Disable dry-run (deletes runtime artifacts)")

    doc_sync = sub.add_parser("doc-sync", help="Run doc synchronization checks")
    doc_sync.add_argument("--check", action="store_true", help="Run workflow/doc hygiene checks")
    doc_sync.add_argument("--agent-map", action="store_true", help="Generate workflow/agent mapping artifacts")
    doc_sync.add_argument("--workspace-scan", action="store_true", help="Run full workspace structure scan")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = _repo_root()
    out_dir = _outputs_dir(repo_root)
    info, warn, error = _try_setup_logger(repo_root)
    info("ops_start", command=args.command, out_dir=str(out_dir))

    try:
        if args.command == "health":
            code = cmd_health(repo_root, out_dir)
        elif args.command == "deploy":
            code = cmd_deploy(repo_root, out_dir, dry_run=bool(args.dry_run))
        elif args.command == "clean":
            code = cmd_clean(repo_root, out_dir, dry_run=bool(args.dry_run))
        elif args.command == "doc-sync":
            code = cmd_doc_sync(
                repo_root,
                out_dir,
                check=bool(args.check),
                agent_map=bool(args.agent_map),
                workspace_scan=bool(args.workspace_scan),
            )
        else:
            parser.print_help()
            code = 2
    except Exception as exc:
        error("ops_failed", err=exc, command=args.command)
        raise

    if code == 0:
        info("ops_finish", command=args.command, status="PASS")
    else:
        warn("ops_finish", command=args.command, status="FAIL", code=code)
    return code


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        raise SystemExit(main())
    raise SystemExit(run_logged_main("ops", "ops", main))
