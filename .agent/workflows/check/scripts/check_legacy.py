#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Add workspace root to sys.path
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]

sys.path.insert(0, str(_repo_root()))

try:
    from lib.check.core.finding import Finding
    # from lib.check.core.confidence import Confidence
    # from lib.check.core.verifier import Verifier
except ImportError:
    # Fallback for direct execution or when libs are missing (handle gracefully)
    pass


@dataclass
class CheckContext:
    repo_root: Path
    timestamp: str
    files: list[Path]
    dependency_graph: dict[str, Any]
    findings: list[Finding]
    plan_id: str


def setup_logger(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")


def _scan_workspace(root: Path) -> list[Path]:
    target_captured: list[Path] = []
    # Scope: .agent/workflows/ and .agent/rules/
    search_dirs = [root / ".agent" / "workflows", root / ".agent" / "rules"]
    
    for d in search_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                if "__pycache__" in p.parts or ".git" in p.parts or p.name.endswith(".pyc"):
                    continue
                target_captured.append(p)
    return target_captured


def _analyze_dependencies(ctx: CheckContext):
    # MVP: Simple file listing for now, extensive dependency graph requires parsing logic
    # which we can add iteratively.
    graph = {"nodes": [], "edges": []}
    for p in ctx.files:
        rel_path = p.relative_to(ctx.repo_root).as_posix()
        node = {
            "id": rel_path,
            "type": "file",
            "path": str(p),
            "last_modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        }
        graph["nodes"].append(node)
        # Future: Parse imports/references to build edges
    ctx.dependency_graph = graph


def _load_rules(root: Path):
    rules_dir = root / ".agent" / "workflows" / "check" / "rules"
    loaded_rules = []
    if not rules_dir.exists():
        return []
    
    sys.path.insert(0, str(rules_dir.parent))
    
    for p in rules_dir.glob("*.py"):
        if p.name == "__init__.py":
            continue
        module_name = f"rules.{p.stem}"
        try:
            # Dynamic import logic would go here
            # For MVP, we might hardcode or use simple importlib
            pass
        except Exception as e:
            logging.warning(f"Failed to load rule {p.name}: {e}")
    return loaded_rules


def _evaluate_rules(ctx: CheckContext):
    # MVP: Identify missing SKILL.md for WORKFLOW.md
    files_set = {str(p.relative_to(ctx.repo_root).as_posix()) for p in ctx.files}
    
    # Rule: missing_skill
    for f in files_set:
        if f.endswith("WORKFLOW.md"):
            skill_path = f.replace("WORKFLOW.md", "SKILL.md")
            if skill_path not in files_set:
                finding = Finding(
                    id=f"missing_skill_{len(ctx.findings)+1}",
                    rule_id="missing_skill",
                    severity="MEDIUM",
                    target={"file": f, "line": 0},
                    message=f"WORKFLOW.md found but SKILL.md missing: {f}",
                    suggestion="Create SKILL.md",
                    auto_fixable=False
                )
                ctx.findings.append(finding)


def _propose(ctx: CheckContext):
    if not ctx.findings:
        print("\n✅ No issues found.")
        return

    print(f"\n## 🔍 /check 結果\n")
    print(f"**plan_id**: `{ctx.plan_id}`")
    print(f"**検出数**: {len(ctx.findings)}件\n")

    for f in ctx.findings:
        icon = "🔴" if f.severity == "HIGH" else "🟡" if f.severity == "MEDIUM" else "⚪"
        print(f"### {icon} {f.severity}: {f.rule_id}")
        print(f"- **ファイル**: `{f.target['file']}`")
        print(f"- **問題**: {f.message}")
        print(f"- **修正案**: {f.suggestion}")
        print("")

    print("---\n**承認する場合**: 「APPROVE」と入力してください\n**修正しない場合**: そのままスキップします")


def main():
    parser = argparse.ArgumentParser(description="Check Agent")
    parser.add_argument("--dry-run", action="store_true", help="Dry run only")
    args = parser.parse_args()
    
    setup_logger()
    
    root = _repo_root()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_id = f"check_{ts}"
    
    ctx = CheckContext(
        repo_root=root,
        timestamp=ts,
        files=[],
        dependency_graph={},
        findings=[],
        plan_id=plan_id
    )

    # 1. SCAN
    logging.info("Step 1: SCAN")
    ctx.files = _scan_workspace(root)
    logging.info(f"Scanned {len(ctx.files)} files")

    # 2. ANALYZE
    logging.info("Step 2: ANALYZE")
    _analyze_dependencies(ctx)

    # 3. DETECT
    logging.info("Step 3: DETECT")
    _evaluate_rules(ctx)

    logging.info("Step 4: PROPOSE")
    _propose(ctx)

    if not ctx.findings:
        return

    if args.dry_run:
        logging.info("Dry run finished.")
        return

    # 5. APPROVE
    try:
        response = input("> ")
    except EOFError:
        response = ""
    
    if response.strip() != "APPROVE":
        logging.info("Skipped execution.")
        return

    # 6. EXECUTE
    logging.info("Step 6: EXECUTE")
    # _execute_fixes(ctx) # MVP: Not implemented yet for complex fixes
    logging.info("Auto-fix execution is not fully implemented in this MVP script.")
    
    # 7. VERIFY
    logging.info("Step 7: VERIFY")
    # re-scan or verify logic
    logging.info("Verification complete.")


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        main()
    else:
        raise SystemExit(run_logged_main("check", "check", main))
