#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — 自己改善ループ MVP

リポジトリの健全性をスキャン→タスク候補生成→1つ選択→(実行/検証)→学習記録→状態保存
を1サイクルとして実行する。

使用例:
    python agi_kernel.py --once --dry-run
    python agi_kernel.py --resume --dry-run
"""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# タイムゾーン: JST
JST = timezone(timedelta(hours=9))

# ワークスペースルートの解決
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_WORKSPACE = _SCRIPT_DIR.parents[2]  # エージェント/AGIカーネル/scripts → root

# ────────────────────────────────────────
# WorkflowLogger統合（lint_workflow_logging_coverage 対応）
# ────────────────────────────────────────
_SHARED_PATH = str(_DEFAULT_WORKSPACE / ".agent" / "workflows" / "shared")
if _SHARED_PATH not in sys.path:
    sys.path.insert(0, _SHARED_PATH)

try:
    from workflow_logging_hook import run_logged_main, logged_main, phase_scope
    _HAS_LOGGER = True
except ImportError:
    _HAS_LOGGER = False

# ────────────────────────────────────────
# KI Learning統合（ライブラリ不在でも落ちない）
# ────────────────────────────────────────
try:
    from ki_learning_hook import report_action_outcome
    _HAS_KI = True
except ImportError:
    _HAS_KI = False


# ============================================================
# 失敗分類
# ============================================================

FAILURE_CATEGORIES = frozenset({
    "TRANSIENT", "DETERMINISTIC", "ENVIRONMENT", "FLAKY", "POLICY"
})
MAX_TASK_FAILURES = 3


def classify_failure(error_msg: str) -> str:
    """エラーメッセージから失敗カテゴリを推定する。"""
    msg = error_msg.lower()
    if any(kw in msg for kw in ("timeout", "timed out", "network", "connection")):
        return "TRANSIENT"
    if any(kw in msg for kw in ("permission", "denied", "policy")):
        return "POLICY"
    if any(kw in msg for kw in ("modulenotfounderror", "importerror", "no module")):
        return "ENVIRONMENT"
    if any(kw in msg for kw in ("flaky", "intermittent", "random")):
        return "FLAKY"
    return "DETERMINISTIC"


# ============================================================
# StateManager — state.json の保存/読込/再開
# ============================================================

class StateManager:
    """AGI Kernelの状態を管理する。"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.state_path = output_dir / "state.json"

    def new_state(self) -> dict[str, Any]:
        """新規サイクル用の初期stateを生成する。"""
        now = datetime.now(JST)
        return {
            "version": __version__,
            "cycle_id": now.strftime("%Y%m%d_%H%M%S"),
            "phase": "BOOT",
            "status": "RUNNING",
            "started_at": now.isoformat(),
            "completed_at": None,
            "scan_results": {},
            "candidates": [],
            "selected_task": None,
            "execution_result": None,
            "verification_result": None,
            "failure_log": [],
            "paused_tasks": [],
        }

    def load(self) -> Optional[dict[str, Any]]:
        """state.jsonを読み込む。存在しなければNone。"""
        if not self.state_path.exists():
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, state: dict[str, Any]) -> None:
        """state.jsonを保存する。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def save_candidates(self, candidates: list[dict], date_str: str) -> Path:
        """日付フォルダにcandidates.jsonを保存する。"""
        day_dir = self.output_dir / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "candidates.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        return path

    def save_report(self, report: dict, date_str: str) -> Path:
        """日付フォルダにreport.jsonを保存する。"""
        day_dir = self.output_dir / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "report.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return path


# ============================================================
# Scanner — workflow_lint / pytest 実行
# ============================================================

class Scanner:
    """リポジトリをスキャンしてタスク候補の元データを収集する。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run_workflow_lint(self) -> dict[str, Any]:
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
            # [ERROR] 行を抽出
            errors = [
                line.strip() for line in result.stdout.splitlines()
                if line.strip().startswith("[ERROR]")
            ]
            return {
                "available": True,
                "errors": len(errors),
                "findings": errors[:20],  # 上限20件
                "exit_code": result.returncode,
            }
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"available": True, "errors": -1, "findings": [], "error": str(e)}

    def run_pytest(self) -> dict[str, Any]:
        """pytestを実行し結果を返す。"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=no"],
                capture_output=True, text=True, timeout=120,
                cwd=str(self.workspace),
            )
            # 失敗数をパース（例: "3 failed, 10 passed"）
            output = result.stdout + result.stderr
            failures = 0
            for line in output.splitlines():
                if "failed" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "failed" or p == "failed,":
                            try:
                                failures = int(parts[i - 1])
                            except (ValueError, IndexError):
                                pass
                            break
            return {
                "available": True,
                "failures": failures,
                "exit_code": result.returncode,
                "summary": output.strip().splitlines()[-1] if output.strip() else "",
            }
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"available": True, "failures": -1, "error": str(e)}


# ============================================================
# タスク候補生成・選択
# ============================================================

def generate_candidates(scan_results: dict[str, Any]) -> list[dict[str, Any]]:
    """スキャン結果からタスク候補を生成する。"""
    candidates = []
    # workflow_lint の ERROR → 優先度1
    lint_data = scan_results.get("workflow_lint", {})
    for i, finding in enumerate(lint_data.get("findings", [])):
        candidates.append({
            "task_id": f"lint_{i:03d}",
            "source": "workflow_lint",
            "priority": 1,
            "title": f"Lint修正: {finding[:80]}",
            "description": finding,
            "estimated_effort": "low",
        })
    # pytest の失敗 → 優先度2
    pytest_data = scan_results.get("pytest", {})
    if pytest_data.get("failures", 0) > 0:
        candidates.append({
            "task_id": "pytest_failures",
            "source": "pytest",
            "priority": 2,
            "title": f"テスト失敗修正 ({pytest_data['failures']}件)",
            "description": pytest_data.get("summary", ""),
            "estimated_effort": "medium",
        })
    return candidates


def select_task(
    candidates: list[dict[str, Any]],
    paused_tasks: list[str],
) -> Optional[dict[str, Any]]:
    """候補から1つだけ選択する（PAUSEDを除外し優先度順）。"""
    active = [c for c in candidates if c["task_id"] not in paused_tasks]
    if not active:
        return None
    active.sort(key=lambda c: c.get("priority", 99))
    return active[0]


# ============================================================
# 失敗ログ管理
# ============================================================

def record_failure(state: dict, task_id: str, category: str, error: str) -> None:
    """失敗をstateに記録し、3回超でPAUSEDにする。"""
    # 既存エントリを探す
    entry = None
    for item in state["failure_log"]:
        if item["task_id"] == task_id:
            entry = item
            break
    if entry is None:
        entry = {"task_id": task_id, "category": category, "count": 0, "last_error": ""}
        state["failure_log"].append(entry)

    entry["count"] += 1
    entry["category"] = category
    entry["last_error"] = error[:500]

    if entry["count"] >= MAX_TASK_FAILURES and task_id not in state["paused_tasks"]:
        state["paused_tasks"].append(task_id)


# ============================================================
# KI Learning 記録
# ============================================================

def _record_ki(outcome: str, **kwargs) -> None:
    """KI Learningに記録する（ライブラリ不在でも落ちない）。"""
    if not _HAS_KI:
        return
    try:
        report_action_outcome(
            agent="/agi_kernel",
            intent_class="agi_kernel_cycle",
            outcome=outcome,
            **kwargs,
        )
    except Exception:
        pass  # ライブラリ不在/エラーでも落ちない


# ============================================================
# メインサイクル
# ============================================================

def run_cycle(args: argparse.Namespace) -> int:
    """1サイクルを実行する。"""
    workspace = Path(args.workspace).resolve()
    output_dir = workspace / "_outputs" / "agi_kernel"
    sm = StateManager(output_dir)

    # ── BOOT ──
    if args.resume:
        state = sm.load()
        if state is None:
            print("[BOOT] state.jsonが見つかりません。新規サイクルを開始します。")
            state = sm.new_state()
        else:
            print(f"[BOOT] state.jsonから再開: cycle_id={state['cycle_id']}, phase={state['phase']}")
            # PAUSEDなら停止
            if state.get("status") == "PAUSED":
                print("[BOOT] ステータスがPAUSEDです。手動でリセットしてください。")
                return 1
            # 前回完了済みなら新規サイクル
            if state.get("status") == "COMPLETED":
                print("[BOOT] 前回サイクルは完了済み。新規サイクルを開始します。")
                state = sm.new_state()
    else:
        state = sm.new_state()

    state["phase"] = "BOOT"
    state["status"] = "RUNNING"
    date_str = datetime.now(JST).strftime("%Y%m%d")

    print(f"[BOOT] サイクル開始: cycle_id={state['cycle_id']}")

    # ── SCAN ──
    state["phase"] = "SCAN"
    print("[SCAN] リポジトリスキャン中...")
    scanner = Scanner(workspace)
    lint_result = scanner.run_workflow_lint()
    pytest_result = scanner.run_pytest()
    state["scan_results"] = {
        "workflow_lint": lint_result,
        "pytest": pytest_result,
        "workflow_lint_errors": lint_result.get("errors", 0),
        "pytest_failures": pytest_result.get("failures", 0),
        "total_issues": max(0, lint_result.get("errors", 0)) + max(0, pytest_result.get("failures", 0)),
    }
    print(f"[SCAN] lint_errors={lint_result.get('errors', 0)}, pytest_failures={pytest_result.get('failures', 0)}")

    # ── SENSE ──
    state["phase"] = "SENSE"
    candidates = generate_candidates(state["scan_results"])
    state["candidates"] = candidates
    sm.save_candidates(candidates, date_str)
    print(f"[SENSE] タスク候補: {len(candidates)}件")

    # ── SELECT ──
    state["phase"] = "SELECT"
    selected = select_task(candidates, state.get("paused_tasks", []))
    state["selected_task"] = selected
    if selected is None:
        print("[SELECT] 対処可能なタスクがありません。サイクル完了。")
        state["status"] = "COMPLETED"
        state["completed_at"] = datetime.now(JST).isoformat()
        state["phase"] = "CHECKPOINT"
        sm.save(state)
        _record_ki("SUCCESS", cycle_id=state["cycle_id"], task_id="none", note="no_candidates")
        print(f"[CHECKPOINT] state保存完了: {sm.state_path}")
        return 0
    print(f"[SELECT] タスク選択: {selected['task_id']} — {selected['title']}")

    # ── EXECUTE ──
    state["phase"] = "EXECUTE"
    if args.dry_run:
        print("[EXECUTE] dry-runモード: スキップ")
        state["execution_result"] = {"dry_run": True, "skipped": True}
    else:
        # MVP: 実行はまだ未実装（将来拡張ポイント）
        print("[EXECUTE] MVP: 自動実行は未実装。タスクレポートのみ出力します。")
        state["execution_result"] = {"implemented": False, "note": "MVP — 手動対応が必要"}

    # ── VERIFY ──
    state["phase"] = "VERIFY"
    if args.dry_run:
        print("[VERIFY] dry-runモード: スキップ")
        state["verification_result"] = {"dry_run": True, "skipped": True}
    else:
        print("[VERIFY] MVP: 自動検証は未実装。")
        state["verification_result"] = {"implemented": False}

    # ── LEARN ──
    state["phase"] = "LEARN"
    outcome = "SUCCESS" if args.dry_run else "PARTIAL"
    _record_ki(
        outcome=outcome,
        cycle_id=state["cycle_id"],
        task_id=selected["task_id"],
        note="dry_run" if args.dry_run else "mvp_partial",
    )
    print(f"[LEARN] KI Learning記録: outcome={outcome}")

    # ── CHECKPOINT ──
    state["phase"] = "CHECKPOINT"
    state["status"] = "COMPLETED"
    state["completed_at"] = datetime.now(JST).isoformat()
    sm.save(state)
    # レポート出力
    report = {
        "cycle_id": state["cycle_id"],
        "status": state["status"],
        "scan_summary": {
            "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
            "pytest_failures": state["scan_results"].get("pytest_failures", 0),
        },
        "candidates_count": len(candidates),
        "selected_task": selected,
        "outcome": outcome,
        "paused_tasks": state.get("paused_tasks", []),
    }
    report_path = sm.save_report(report, date_str)
    print(f"[CHECKPOINT] state保存完了: {sm.state_path}")
    print(f"[CHECKPOINT] レポート出力: {report_path}")
    return 0


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AGI Kernel — 自己改善ループ MVP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="1サイクルのみ実行して終了",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="state.jsonから再開",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="EXECUTE/VERIFYフェーズをスキップ",
    )
    parser.add_argument(
        "--workspace", type=str, default=str(_DEFAULT_WORKSPACE),
        help=f"ワークスペースルート（デフォルト: {_DEFAULT_WORKSPACE}）",
    )
    return parser


def main() -> int:
    """エントリーポイント。"""
    parser = build_parser()
    args = parser.parse_args()
    return run_cycle(args)


if __name__ == "__main__":
    if _HAS_LOGGER:
        # WorkflowLogger統合: run_logged_main で実行
        exit_code = run_logged_main(
            agent="agi_kernel",
            workflow="agi_kernel",
            main_func=main,
            phase_name="AGI_KERNEL_CYCLE",
        )
        raise SystemExit(exit_code)
    else:
        raise SystemExit(main())
