#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

WORKFLOW_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WORKFLOW_ROOT.parents[2]
if str(WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_ROOT))

from core.confidence import calculate_confidence, detect_reference_type  # type: ignore
from core.finding import Edge, Finding, Severity  # type: ignore
from rules.cycle_dependency import detect_cycle_dependency  # type: ignore
from rules.dangling_reference import detect_dangling_reference  # type: ignore
from rules.invalid_yaml_json import detect_invalid_yaml_json, parse_file  # type: ignore
from rules.path_escape import detect_path_escape, resolve_reference  # type: ignore
from rules.schema_mismatch import detect_schema_mismatch  # type: ignore


def _outputs_dir() -> Path:
    day = datetime.now().strftime("%Y%m%d")
    output_dir = REPO_ROOT / "_outputs" / "check" / day
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _try_setup_logger():
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from lib.logger import setup_logger, info, warn, error  # type: ignore

        setup_logger(path=REPO_ROOT / "_logs" / "check.jsonl")
        return info, warn, error
    except Exception as exc:
        import warnings
        warnings.warn(f"[check] ロガー初期化失敗: {type(exc).__name__}: {exc}", stacklevel=2)

        def _noop(*args: Any, **kwargs: Any) -> None:
            return

        return _noop, _noop, _noop


def _scan_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in {".py", ".md", ".json", ".yaml", ".yml"}:
            files.append(path)
    return files


def _line_from_index(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _module_to_path(module: str) -> Path | None:
    cleaned = module.strip()
    if not cleaned or cleaned.startswith("."):
        return None
    pieces = cleaned.split(".")
    candidate_file = WORKFLOW_ROOT / Path(*pieces).with_suffix(".py")
    if candidate_file.exists():
        return candidate_file
    candidate_package = WORKFLOW_ROOT / Path(*pieces) / "__init__.py"
    if candidate_package.exists():
        return candidate_package
    repo_candidate_file = REPO_ROOT / Path(*pieces).with_suffix(".py")
    if repo_candidate_file.exists():
        return repo_candidate_file
    repo_candidate_package = REPO_ROOT / Path(*pieces) / "__init__.py"
    if repo_candidate_package.exists():
        return repo_candidate_package
    return None


def _extract_python_import_edges(path: Path, content: str) -> list[Edge]:
    edges: list[Edge] = []
    pattern = re.compile(r"^\s*(?:from\s+([a-zA-Z0-9_\.]+)\s+import|import\s+([a-zA-Z0-9_\.]+))", re.MULTILINE)
    for match in pattern.finditer(content):
        module_name = match.group(1) or match.group(2) or ""
        target_path = _module_to_path(module_name)
        if target_path is None:
            continue
        line = _line_from_index(content, match.start())
        confidence = calculate_confidence(
            ref_type=detect_reference_type(match.group(0), module_name),
            target_exists=target_path.exists(),
        )
        edges.append(
            Edge(
                src_file=str(path),
                dst_file=str(target_path),
                edge_type="import",
                raw_target=module_name,
                confidence=confidence,
                line_range=(line, line),
                snippet=match.group(0).strip(),
            )
        )
    return edges


def _extract_path_reference_edges(path: Path, content: str) -> list[Edge]:
    edges: list[Edge] = []
    pattern = re.compile(r"\]\(([^)#\s]+)\)|['\"]([^'\"]+\.(?:md|py|json|yaml|yml))['\"]")
    for match in pattern.finditer(content):
        raw_target = (match.group(1) or match.group(2) or "").strip()
        if not raw_target:
            continue
        line = _line_from_index(content, match.start())
        resolved = resolve_reference(base_file=str(path), raw_ref=raw_target, root=str(REPO_ROOT))
        destination = resolved.normalized if resolved.normalized else raw_target
        confidence = calculate_confidence(
            ref_type=detect_reference_type(match.group(0), raw_target),
            target_exists=Path(destination).exists() if Path(destination).is_absolute() else False,
        )
        edge_type = "markdown_link" if raw_target in (match.group(1) or "") else "path_literal"
        edges.append(
            Edge(
                src_file=str(path),
                dst_file=str(destination),
                edge_type=edge_type,
                raw_target=raw_target,
                confidence=confidence,
                line_range=(line, line),
                snippet=match.group(0),
            )
        )
    return edges


def _extract_edges(files: list[Path]) -> list[Edge]:
    edges: list[Edge] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if path.suffix.lower() == ".py":
            edges.extend(_extract_python_import_edges(path=path, content=content))
        edges.extend(_extract_path_reference_edges(path=path, content=content))
    return edges


def _build_graph(edges: list[Edge], known_files: set[str]) -> dict[str, list[tuple[str, Edge]]]:
    graph: dict[str, list[tuple[str, Edge]]] = {}
    for edge in edges:
        if edge.dst_file not in known_files:
            continue
        graph.setdefault(edge.src_file, []).append((edge.dst_file, edge))
    return graph


def _parsed_objects(files: list[Path]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for path in files:
        parser_type, parsed, error = parse_file(str(path))
        if error is not None:
            continue
        if isinstance(parsed, dict):
            objects.append({"file_path": str(path), "obj": parsed, "parser_type": parser_type})
    return objects


def _summarize(findings: list[Finding]) -> dict[str, Any]:
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity.value] += 1
        by_rule[finding.rule_id] = by_rule.get(finding.rule_id, 0) + 1
    return {"total": len(findings), "by_severity": by_severity, "by_rule": by_rule}


def run_check(root: Path) -> dict[str, Any]:
    files = _scan_files(root=root)
    file_strings = [str(item) for item in files]
    known_files = set(file_strings)
    edges = _extract_edges(files=files)
    graph = _build_graph(edges=edges, known_files=known_files)

    findings: list[Finding] = []
    findings.extend(detect_invalid_yaml_json(file_strings))
    findings.extend(detect_schema_mismatch(_parsed_objects(files)))
    findings.extend(detect_path_escape(edges=edges, root=str(root)))
    findings.extend(detect_dangling_reference(edges=edges, existing_files=file_strings))
    findings.extend(detect_cycle_dependency(graph))

    summary = _summarize(findings=findings)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "stats": {
            "files_scanned": len(files),
            "edges_detected": len(edges),
        },
        "summary": summary,
        "findings": [finding.to_dict() for finding in findings],
    }
    return payload


def _write_report(payload: dict[str, Any]) -> Path:
    report_path = _outputs_dir() / "check_report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def _should_fail(payload: dict[str, Any], fail_on: str) -> bool:
    severity = payload["summary"]["by_severity"]
    if fail_on == "none":
        return False
    if fail_on == "high":
        return severity["high"] > 0
    if fail_on == "medium":
        return severity["high"] > 0 or severity["medium"] > 0
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Check workflow consistency and references")
    parser.add_argument("--root", default=str(REPO_ROOT / ".agent" / "workflows"), help="Root path to scan")
    parser.add_argument("--print-json", action="store_true", help="Print full json report")
    parser.add_argument("--fail-on", choices=["high", "medium", "none"], default="high", help="Failure threshold")
    args = parser.parse_args()

    info, warn, error = _try_setup_logger()
    info("check_start", root=args.root, fail_on=args.fail_on)
    try:
        payload = run_check(Path(args.root))
    except Exception as exc:
        error("check_failed", err=exc, root=args.root)
        raise

    report_path = _write_report(payload)
    summary = payload["summary"]
    if _should_fail(payload, args.fail_on):
        warn("check_findings", summary=summary, report_path=str(report_path))
        exit_code = 1
    else:
        info("check_ok", summary=summary, report_path=str(report_path))
        exit_code = 0

    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"report_path": str(report_path), "summary": summary}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve()
    for _parent in _repo_root.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("check", "check", main, phase_name="CHECK_RUN"))

