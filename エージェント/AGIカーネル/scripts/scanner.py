#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — スキャナーモジュール (v0.6.1)

pytest出力パーサー、Scanner、候補生成・選択。
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agi_kernel")


# ============================================================
# pytest出力パーサー（pure function — テスト可能）
# ============================================================

# ANSIエスケープシーケンス除去（--color=no の保険）
_RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """ANSIカラーコードを除去する。"""
    return _RE_ANSI.sub("", text)


# errors_count 抽出用パターン
_RE_ERRORS_IN = re.compile(r"(?:^|\s)(\d+)\s+errors?\s+in\s", re.MULTILINE)
_RE_INTERRUPTED = re.compile(r"Interrupted:\s+(\d+)\s+errors?\s+during\s+collection")

# summary行（結果行）判定用キーワード
_SUMMARY_KEYWORDS = ("passed", "failed", "error", "no tests ran")

# headline 候補抽出用キーワード
_EXCEPTION_NAMES = (
    "ModuleNotFoundError", "ImportError", "ConnectionRefusedError",
    "FileNotFoundError", "SyntaxError", "AttributeError",
    "TypeError", "NameError", "OSError", "PermissionError",
)


def _extract_headline(lines: list[str]) -> str:
    """出力行からエラー原因を示す最適な1行を抽出する。"""
    for line in lines:
        if "ERROR collecting" in line:
            return line.strip()
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("E   "):
            return stripped
    for line in lines:
        for exc in _EXCEPTION_NAMES:
            if exc in line:
                return line.strip()
    for line in lines:
        if "Interrupted:" in line and "errors" in line:
            return line.strip()
    return lines[-1].strip() if lines else ""


def _extract_error_lines(lines: list[str], max_lines: int = 10) -> list[str]:
    """'ERROR' で始まる行を最大N行抽出する。"""
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ERROR") or stripped.startswith("E   "):
            result.append(stripped)
            if len(result) >= max_lines:
                break
    return result


# ── テスト失敗ノード抽出（nodeid単位分割用） ──
_RE_FAILED_LINE = re.compile(r"^FAILED\s+(.+?)\s*(?:-\s*.+)?$")
_RE_ERROR_LINE = re.compile(r"^ERROR\s+(.+?)\s*(?:-\s*.+)?$")


def _extract_failure_nodes(lines: list[str], max_nodes: int = 30) -> list[dict]:
    """FAILED / ERROR 行から nodeid と path を抽出する。

    Returns:
        [{"nodeid": "tests/test_foo.py::TestX::test_y", "path": "tests/test_foo.py"}, ...]
    """
    nodes: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        m = _RE_FAILED_LINE.match(stripped)
        if not m:
            m = _RE_ERROR_LINE.match(stripped)
        if m:
            nodeid = m.group(1).strip()
            if nodeid in seen:
                continue
            seen.add(nodeid)
            path = nodeid.split("::")[0]
            nodes.append({"nodeid": nodeid, "path": path})
            if len(nodes) >= max_nodes:
                break
    return nodes


# ── エラーブロック抽出（ファイル単位の収集エラー分割用） ──
_RE_ERROR_COLLECTING = re.compile(r"ERROR\s+collecting\s+(.+?)(?:\s+|$)")


def _extract_error_blocks(lines: list[str], max_blocks: int = 20) -> list[dict]:
    """ERROR collecting 行をファイル単位でブロック化する。

    各ブロック: {"path": str, "exception_line": str, "snippet": list[str]}
    """
    blocks: list[dict] = []
    i = 0
    while i < len(lines) and len(blocks) < max_blocks:
        stripped = lines[i].strip()
        m = _RE_ERROR_COLLECTING.match(stripped)
        if m:
            path = m.group(1).strip()
            exception_line = ""
            snippet = [stripped]
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith("E   "):
                    snippet.append(next_line)
                    if not exception_line:
                        exception_line = next_line
                    j += 1
                elif next_line.startswith("ERROR") or not next_line:
                    break
                else:
                    snippet.append(next_line)
                    j += 1
            blocks.append({
                "path": path,
                "exception_line": exception_line,
                "snippet": snippet[:8],
            })
            i = j
        else:
            i += 1
    return blocks


def _stable_task_id(prefix: str, *parts: str) -> str:
    """安定したtask_idを生成する（sha1先頭10文字）。"""
    key = ":".join(parts)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _find_summary_line(lines: list[str]) -> str:
    """末尾から逆走査して結果行を探す。warningsではなくpassed/failed/error行を優先。"""
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if "warnings summary" in stripped.lower() or "Warning:" in stripped:
            continue
        lower = stripped.lower()
        for kw in _SUMMARY_KEYWORDS:
            if kw in lower:
                return stripped
    return lines[-1].strip() if lines else ""


def parse_pytest_result(output: str, exit_code: int) -> dict[str, Any]:
    """pytest出力とexit_codeからスキャン結果dictを生成する。

    Pure function。subprocess を呼ばずにユニットテスト可能。
    """
    clean_output = strip_ansi(output) if output else ""
    lines = clean_output.strip().splitlines() if clean_output.strip() else []
    tail = lines[-20:] if lines else []

    summary = _find_summary_line(lines) if lines else ""
    failures = 0
    for line in lines:
        if "failed" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p in ("failed", "failed,"):
                    try:
                        failures = int(parts[i - 1])
                    except (ValueError, IndexError):
                        pass
                    break
            if failures > 0:
                break

    errors_count = 0
    m = _RE_ERRORS_IN.search(clean_output)
    if m:
        errors_count = int(m.group(1))
    else:
        m = _RE_INTERRUPTED.search(clean_output)
        if m:
            errors_count = int(m.group(1))

    if exit_code == 1 and failures == 0:
        failures = 1
    if exit_code == 2 and errors_count == 0:
        errors_count = 1

    headline = _extract_headline(lines) if lines else ""
    error_lines = _extract_error_lines(lines)
    error_blocks = _extract_error_blocks(lines) if exit_code == 2 and lines else []
    failure_nodes = _extract_failure_nodes(lines) if exit_code == 1 and lines else []

    return {
        "available": True,
        "failures": failures,
        "exit_code": exit_code,
        "summary": summary,
        "tail": tail,
        "headline": headline,
        "errors_count": errors_count,
        "error_lines": error_lines,
        "error_blocks": error_blocks,
        "failure_nodes": failure_nodes,
    }


# ============================================================
# Scanner — workflow_lint / pytest 実行
# ============================================================

class Scanner:
    """リポジトリをスキャンしてタスク候補の元データを収集する。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run_workflow_lint(self, severity_filter: tuple[str, ...] = ("[ERROR]",)) -> dict[str, Any]:
        """workflow_lintを実行し結果を返す。"""
        lint_script = self.workspace / "tools" / "workflow_lint.py"
        if not lint_script.exists():
            return {"available": False, "errors": 0, "findings": []}
        try:
            result = subprocess.run(
                [sys.executable, str(lint_script)],
                capture_output=True, text=True, timeout=60,
                cwd=str(self.workspace),
            )
            findings = [
                line.strip() for line in result.stdout.splitlines()
                if any(line.strip().startswith(sev) for sev in severity_filter)
            ]
            return {
                "available": True,
                "errors": len(findings),
                "findings": findings[:20],
                "exit_code": result.returncode,
                "severity_filter": list(severity_filter),
            }
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"available": True, "errors": -1, "findings": [], "error": str(e)}

    def run_pytest(self) -> dict[str, Any]:
        """pytestを実行し結果を返す。"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"],
                capture_output=True, text=True, timeout=120,
                cwd=str(self.workspace),
            )
            output = result.stdout + result.stderr
            return parse_pytest_result(output, result.returncode)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"available": True, "failures": -1, "exit_code": -1, "error": str(e), "tail": []}


# ============================================================
# タスク候補生成・選択
# ============================================================

def _build_pytest_description(pytest_data: dict[str, Any]) -> str:
    """pytest候補のdescriptionを構築する。headline + error_lines。"""
    headline = pytest_data.get("headline", "") or pytest_data.get("summary", "")
    error_lines = pytest_data.get("error_lines", [])
    parts = [headline]
    if error_lines:
        parts.append("")
        parts.extend(error_lines[:5])
    desc = "\n".join(parts)
    return desc[:800]


def _extract_lint_target_path(finding: str) -> str:
    """lint finding文字列からターゲットファイルパスを抽出する。

    形式例: [ERROR] C:/path/to/file: description (WL-XXX-YYY)
             [ERROR] agent 'name': description (WL-XXX-YYY)
    パスはドライブレター(X:/) または相対パスで始まる部分を検出。
    """
    # [SEVERITY] の後の部分を取得
    m = re.match(r"\[(?:ERROR|CAUTION|WARNING)\]\s+(.+)", finding)
    if not m:
        return ""
    rest = m.group(1)
    # ドライブレター付き絶対パス (C:/... : description)
    m2 = re.match(r"([A-Za-z]:/[^:]+?):\s", rest)
    if m2:
        return m2.group(1).strip()
    # agent 'name': の場合はパス不明
    return ""


def generate_candidates(scan_results: dict[str, Any]) -> list[dict[str, Any]]:
    """スキャン結果からタスク候補を生成する。"""
    candidates = []
    lint_data = scan_results.get("workflow_lint", {})
    for finding in lint_data.get("findings", []):
        task_id = _stable_task_id("lint", finding[:200])
        target_path = _extract_lint_target_path(finding)
        candidate: dict[str, Any] = {
            "task_id": task_id,
            "source": "workflow_lint",
            "priority": 1,
            "title": f"Lint修正: {finding[:80]}",
            "description": finding,
            "estimated_effort": "low",
        }
        if target_path:
            candidate["target_path"] = target_path
        candidates.append(candidate)

    pytest_data = scan_results.get("pytest", {})
    pytest_exit = pytest_data.get("exit_code", 0)
    pytest_failures = pytest_data.get("failures", 0)
    errors_count = pytest_data.get("errors_count", 0)
    error_blocks = pytest_data.get("error_blocks", [])
    failure_nodes = pytest_data.get("failure_nodes", [])

    if errors_count > 0 and error_blocks:
        for blk in error_blocks:
            path = blk["path"]
            exc = blk.get("exception_line", "")
            task_id = _stable_task_id("pytest_ce", path, exc)
            snippet_desc = "\n".join(blk.get("snippet", []))
            candidates.append({
                "task_id": task_id,
                "source": "pytest",
                "priority": 2,
                "title": f"収集エラー修正: {path}",
                "description": snippet_desc[:800],
                "estimated_effort": "medium",
                "target_path": path,
            })
    elif errors_count > 0:
        description = _build_pytest_description(pytest_data)
        task_id = _stable_task_id("pytest_ce", str(errors_count))
        candidates.append({
            "task_id": task_id,
            "source": "pytest",
            "priority": 2,
            "title": f"収集エラー修正 ({errors_count}件)",
            "description": description,
            "estimated_effort": "medium",
        })

    if pytest_failures > 0 and failure_nodes:
        for node in failure_nodes:
            nodeid = node["nodeid"]
            path = node["path"]
            task_id = _stable_task_id("pytest_tf", nodeid)
            candidates.append({
                "task_id": task_id,
                "source": "pytest",
                "priority": 2,
                "title": f"テスト失敗修正: {nodeid[:80]}",
                "description": f"FAILED {nodeid}",
                "estimated_effort": "medium",
                "target_path": path,
                "target_nodeid": nodeid,
            })
    elif pytest_failures > 0:
        description = _build_pytest_description(pytest_data)
        task_id = _stable_task_id("pytest_tf", str(pytest_failures))
        candidates.append({
            "task_id": task_id,
            "source": "pytest",
            "priority": 2,
            "title": f"テスト失敗修正 ({pytest_failures}件)",
            "description": description,
            "estimated_effort": "medium",
        })
    elif pytest_exit not in (0, None) and pytest_exit != -1 and errors_count == 0:
        description = _build_pytest_description(pytest_data)
        task_id = _stable_task_id("pytest_unk", str(pytest_exit))
        candidates.append({
            "task_id": task_id,
            "source": "pytest",
            "priority": 2,
            "title": f"pytest異常終了 (exit_code={pytest_exit})",
            "description": description,
            "estimated_effort": "medium",
        })
    return candidates


# ── auto_fixable 判定用パターン ──
_LINT_UNFIXABLE_PATTERNS = [
    "missing skill.md",
    "missing workflow.md",
    "utf-8",
    "decode",
    "__pycache__",
]


def annotate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補に auto_fixable / blocked_reason を付与する。"""
    for c in candidates:
        source = c.get("source", "")
        fixable = True
        reason = ""
        if source == "pytest":
            if not c.get("target_nodeid") and not c.get("target_path"):
                fixable = False
                reason = "no_target_specified"
        elif source == "workflow_lint":
            desc_lower = c.get("description", "").lower()
            for pattern in _LINT_UNFIXABLE_PATTERNS:
                if pattern in desc_lower:
                    fixable = False
                    reason = f"unfixable_lint:{pattern}"
                    break
        c["auto_fixable"] = fixable
        c["blocked_reason"] = reason
    return candidates


def select_task(
    candidates: list[dict[str, Any]],
    paused_tasks: list[str],
) -> Optional[dict[str, Any]]:
    """候補から1つだけ選択する（PAUSED除外 + auto_fixable フィルタ + 優先度順）。"""
    active = [
        c for c in candidates
        if c["task_id"] not in paused_tasks and c.get("auto_fixable", True)
    ]
    if not active:
        return None
    active.sort(key=lambda c: c.get("priority", 99))
    return active[0]
