#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — 自己改善ループ (v0.6.3)

リポジトリの健全性をスキャン→タスク候補生成→1つ選択→(実行/検証)→学習記録→状態保存
を1サイクルとして実行する。

終了コード:
  EXIT_SUCCESS  (0) — 正常完了 / FAILURE後のロールバック完了
  EXIT_PAUSED   (1) — タスクが3回失敗しPAUSED / env blocker / resume PAUSED
  EXIT_LOCK     (2) — 別プロセスがロック保持中

使用例:
    python agi_kernel.py --once --dry-run
    python agi_kernel.py --resume --dry-run
    python agi_kernel.py --workspaces /repo1 /repo2 --dry-run
"""

from __future__ import annotations

__version__ = "0.6.3"

# ── 終了コード定数 ──
EXIT_SUCCESS = 0   # 正常完了 / FAILURE後のロールバック完了
EXIT_PAUSED = 1    # PAUSED（3回失敗）/ BLOCKED / resume PAUSED
EXIT_LOCK = 2      # 別プロセスがロック保持中

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# ── .env 自動読み込み ──
try:
    from dotenv import load_dotenv
    _dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
    if os.path.isfile(_dotenv_path):
        load_dotenv(_dotenv_path, override=False)
except ImportError:
    pass

# ── ロガー初期化 ──
logger = logging.getLogger("agi_kernel")

# ── サブモジュール import ──
from state import (  # noqa: E402
    JST,
    FAILURE_CATEGORIES,
    MAX_TASK_FAILURES,
    LOCK_TTL_SECONDS,
    FileLock,
    StateManager,
    classify_failure,
    record_failure,
    record_ki,
)
from scanner import (  # noqa: E402
    strip_ansi,
    parse_pytest_result,
    Scanner,
    generate_candidates,
    annotate_candidates,
    select_task,
    _extract_error_blocks,
    _extract_failure_nodes,
    _stable_task_id,
)
from executor import (  # noqa: E402
    MAX_PATCH_FILES,
    MAX_DIFF_LINES,
    MAX_LLM_RETRIES,
    COMMAND_ALLOWLIST,
    _COST_PER_1M,
    GeminiClient,
    get_genai_client,
    log_token_usage,
    Executor,
    GeminiExecutor,
    parse_patch_json,
    validate_patch_result,
    apply_patch,
    preflight_check,
    backup_targets,
    rollback_with_backup,
    restore_rollback_context,
    compute_patch_diff_lines,
    build_execute_context,
)
from verifier import Verifier  # noqa: E402
from webhook import send_webhook  # noqa: E402

# ── 後方互換エイリアス（テスト・外部コード向け） ──
# v0.6.0以前は全関数が agi_kernel.py に _prefix 付きで定義されていた。
# 既存テストがこれらの名前でimportしているため、エイリアスを維持する。
_validate_patch_result = validate_patch_result
_apply_patch = apply_patch
_parse_patch_json = parse_patch_json
_preflight_check = preflight_check
_backup_targets = backup_targets
_rollback_with_backup = rollback_with_backup
_compute_patch_diff_lines = compute_patch_diff_lines
_restore_rollback_context = restore_rollback_context
_log_token_usage = log_token_usage
_record_ki = record_ki
_send_webhook = send_webhook
_GeminiClientCompat = GeminiClient  # 旧名
COST_PER_1M = _COST_PER_1M  # テスト互換エイリアス（パブリック名）

# ── WorkflowLogger統合 ──
_HAS_LOGGER = False
try:
    _SCRIPT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_SCRIPT_DIR.parents[2] / ".agent" / "workflows" / "shared"))
    from workflow_logger import run_logged_main  # type: ignore
    _HAS_LOGGER = True
except ImportError:
    pass


# ============================================================
# JSON構造化ログフォーマッタ
# ============================================================

class _JsonFormatter(logging.Formatter):
    """JSON構造化ログフォーマッタ。"""
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "phase"):
            entry["phase"] = record.phase
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging(*, json_mode: bool = False, level: int = logging.INFO) -> None:
    """ロギング初期設定。json_mode=True でJSON構造化出力。"""
    root_logger = logging.getLogger("agi_kernel")
    root_logger.setLevel(level)
    # 既存ハンドラをクリアして再設定（テスト時の再呼び出しに対応）
    root_logger.handlers.clear()
    handler = logging.StreamHandler()
    if json_mode:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)


# ============================================================
# ワークスペースルートの解決
# ============================================================

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_WORKSPACE = _SCRIPT_DIR.parents[2]  # エージェント/AGIカーネル/scripts → root


# ============================================================
# フェーズ順序定義（--resume 用）
# ============================================================

PHASE_ORDER = ["BOOT", "SCAN", "SENSE", "SELECT", "EXECUTE", "VERIFY", "LEARN", "CHECKPOINT"]


def _should_skip_phase(last_completed: str, target_phase: str) -> bool:
    """resume時、target_phaseが既に完了済みかを判定する。"""
    try:
        completed_idx = PHASE_ORDER.index(last_completed)
        target_idx = PHASE_ORDER.index(target_phase)
    except ValueError:
        return False
    return target_idx <= completed_idx


# ============================================================
# メインサイクル
# ============================================================

def run_cycle(args: argparse.Namespace, workspace: Path | None = None) -> int:
    """1サイクルを実行する。

    Args:
        args: CLIパース済み引数
        workspace: 対象ワークスペース（Noneならargs.workspaceを使用）
    """
    ws = workspace or Path(args.workspace).resolve()
    output_dir = ws / "_outputs" / "agi_kernel"
    sm = StateManager(output_dir)

    # ── LOCK ──
    lock = FileLock(output_dir / "lock")
    if not lock.acquire():
        logger.warning("[LOCK] 別のAGI Kernelプロセスが実行中です。終了します。")
        return EXIT_LOCK

    try:
        return _run_cycle_inner(args, ws, output_dir, sm)
    finally:
        lock.release()


def _run_cycle_inner(
    args: argparse.Namespace,
    workspace: Path,
    output_dir: Path,
    sm: StateManager,
) -> int:
    """ロック取得後の内部サイクル実行。"""
    resume_phase: Optional[str] = None

    # ── BOOT ──
    if args.resume:
        state = sm.load()
        if state is None:
            logger.info("[BOOT] state.jsonが見つかりません。新規サイクルを開始します。")
            state = sm.new_state()
        else:
            logger.info(f"[BOOT] state.jsonから再開: cycle_id={state['cycle_id']}, phase={state['phase']}")
            if state.get("status") == "PAUSED":
                logger.warning("[BOOT] ステータスがPAUSEDです。手動でリセットしてください。")
                return EXIT_PAUSED
            if state.get("status") == "COMPLETED":
                logger.info("[BOOT] 前回サイクルは完了済み。新規サイクルを開始します。")
                state = sm.new_state()
            else:
                resume_phase = state.get("last_completed_phase")
                if resume_phase is None:
                    old_phase = state.get("phase", "BOOT")
                    try:
                        idx = PHASE_ORDER.index(old_phase)
                        resume_phase = PHASE_ORDER[idx - 1] if idx > 0 else None
                    except ValueError:
                        resume_phase = None
                if resume_phase:
                    logger.info(f"[BOOT] last_completed_phase={resume_phase} の次から再開します。")
                else:
                    logger.info("[BOOT] 完了済みフェーズなし。最初から実行します。")
    else:
        state = sm.new_state()

    state["phase"] = "BOOT"
    state["last_completed_phase"] = None
    state["status"] = "RUNNING"
    date_str = datetime.now(JST).strftime("%Y%m%d")

    logger.info(f"[BOOT] サイクル開始: cycle_id={state['cycle_id']}")
    state["last_completed_phase"] = "BOOT"
    sm.save(state)

    # ── SCAN ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SCAN")):
        state["phase"] = "SCAN"
        logger.info("[SCAN] リポジトリスキャン中...")
        scanner = Scanner(workspace)
        sev_raw = getattr(args, "lint_severity", "error")
        sev_filter = tuple(f"[{s.strip().upper()}]" for s in sev_raw.split(","))
        lint_result = scanner.run_workflow_lint(severity_filter=sev_filter)
        pytest_result = scanner.run_pytest()
        _lint_errors = max(0, lint_result.get("errors", 0))
        _pytest_errors = max(0, pytest_result.get("errors_count", 0))
        _pytest_failures = max(0, pytest_result.get("failures", 0))
        state["scan_results"] = {
            "workflow_lint": lint_result,
            "pytest": pytest_result,
            "workflow_lint_errors": _lint_errors,
            "pytest_errors": _pytest_errors,
            "pytest_failures": _pytest_failures,
            "total_issues": _lint_errors + _pytest_errors + _pytest_failures,
        }
        logger.info(f"[SCAN] lint_errors={_lint_errors}, pytest_errors={_pytest_errors}, pytest_failures={_pytest_failures}")
        state["last_completed_phase"] = "SCAN"
        sm.save(state)
    else:
        logger.info("[SCAN] resume: スキップ（完了済み）")

    # ── SENSE ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SENSE")):
        state["phase"] = "SENSE"
        candidates = generate_candidates(state["scan_results"])
        state["candidates"] = candidates
        annotate_candidates(candidates)
        sm.save_candidates(candidates, date_str, state["cycle_id"])
        logger.info(f"[SENSE] タスク候補: {len(candidates)}件")
        blocked = [c for c in candidates if not c.get("auto_fixable", True)]
        fixable = [c for c in candidates if c.get("auto_fixable", True)]
        if blocked:
            logger.info(f"[SENSE] auto_fixable=false: {len(blocked)}件 (blocked)")
        logger.info(f"[SENSE] auto_fixable=true: {len(fixable)}件 (対処可能)")
        state["last_completed_phase"] = "SENSE"
        sm.save(state)
    else:
        logger.info("[SENSE] resume: スキップ（完了済み）")
        candidates = state.get("candidates", [])

    # ── SELECT ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SELECT")):
        state["phase"] = "SELECT"
        selected = select_task(candidates, state.get("paused_tasks", []))
        state["selected_task"] = selected
        if selected is None:
            blocked = [c for c in candidates if not c.get("auto_fixable", True)]
            reason = "no_fixable_candidates" if blocked else "no_candidates"
            logger.info(f"[SELECT] 対処可能なタスクがありません（{reason}）。サイクル完了。")
            state["status"] = "COMPLETED"
            state["completed_at"] = datetime.now(JST).isoformat()
            state["phase"] = "CHECKPOINT"
            state["last_completed_phase"] = "CHECKPOINT"
            sm.save(state)
            report = {
                "cycle_id": state["cycle_id"],
                "status": state["status"],
                "reason": reason,
                "scan_summary": {
                    "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
                    "pytest_errors": state["scan_results"].get("pytest_errors", 0),
                    "pytest_failures": state["scan_results"].get("pytest_failures", 0),
                },
                "candidates_count": len(candidates),
                "blocked_candidates": [
                    {"task_id": c["task_id"], "title": c["title"], "blocked_reason": c.get("blocked_reason", "")}
                    for c in blocked
                ],
                "selected_task": None,
                "outcome": "SUCCESS",
                "paused_tasks": state.get("paused_tasks", []),
            }
            sm.save_report(report, date_str, state["cycle_id"])
            record_ki("SUCCESS", cycle_id=state["cycle_id"], task_id="none", note=reason)
            logger.info(f"[CHECKPOINT] state保存完了: {sm.state_path}")
            return 0
        logger.info(f"[SELECT] タスク選択: {selected['task_id']} — {selected['title']}")
        state["last_completed_phase"] = "SELECT"
        sm.save(state)
    else:
        logger.info("[SELECT] resume: スキップ（完了済み）")
        selected = state.get("selected_task")

    # ── EXECUTE ──
    modified_paths: list[Path] = []
    backup_map: dict[str, Optional[Path]] = {}
    if not (resume_phase and _should_skip_phase(resume_phase, "EXECUTE")):
        state["phase"] = "EXECUTE"
        if args.dry_run:
            logger.info("[EXECUTE] dry-runモード: スキップ")
            state["execution_result"] = {"dry_run": True, "skipped": True}
        else:
            # ── Preflight ──
            preflight = preflight_check(workspace)
            if not preflight["ok"]:
                reason = preflight["reason"]
                logger.error(f"[EXECUTE] ❌ Preflight失敗 (環境ブロッカー): {reason}")
                state["status"] = "PAUSED"
                state["completed_at"] = datetime.now(JST).isoformat()
                state["phase"] = "CHECKPOINT"
                state["last_completed_phase"] = "CHECKPOINT"
                sm.save(state)
                report = {
                    "cycle_id": state["cycle_id"],
                    "status": "PAUSED",
                    "reason": f"blocked_by_{reason}",
                    "scan_summary": {
                        "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
                        "pytest_errors": state["scan_results"].get("pytest_errors", 0),
                        "pytest_failures": state["scan_results"].get("pytest_failures", 0),
                    },
                    "candidates_count": len(candidates),
                    "selected_task": selected,
                    "outcome": "BLOCKED",
                    "paused_tasks": state.get("paused_tasks", []),
                }
                sm.save_report(report, date_str, state["cycle_id"])
                record_ki("FAILURE", cycle_id=state["cycle_id"],
                          task_id=selected["task_id"] if selected else "none",
                          note=f"env_blocker:{reason}")
                return EXIT_PAUSED
            else:
                if not preflight["git_available"]:
                    logger.warning("[EXECUTE] ⚠️ git不在 — difflibベースで安全弁を適用")

                # ── LLMパッチ生成→バックアップ→適用→diff検証 ──
                logger.info("[EXECUTE] LLMパッチ生成を開始...")
                try:
                    model_name = getattr(args, "llm_model", None) or "gemini-2.5-flash"
                    strong_name = getattr(args, "llm_strong_model", None) or "gemini-2.5-pro"
                    executor = GeminiExecutor(
                        model_name=model_name,
                        strong_model_name=strong_name,
                        state=state,
                    )
                    context = build_execute_context(selected, state["scan_results"], workspace)
                    patch = executor.generate_patch(selected, context, workspace)
                    logger.info(f"[EXECUTE] パッチ生成完了: {len(patch['files'])}ファイル")
                    logger.info(f"[EXECUTE] 説明: {patch.get('explanation', '')[:200]}")

                    # バックアップ作成
                    date_str = datetime.now(JST).strftime("%Y%m%d")
                    bak_dir = output_dir / date_str / state["cycle_id"] / "backup"
                    backup_map = backup_targets(patch, workspace, bak_dir)
                    logger.info(f"[EXECUTE] バックアップ完了: {bak_dir}")

                    # --approve ゲート
                    if getattr(args, "approve", False):
                        logger.info("=" * 60)
                        logger.info("[APPROVE] パッチ内容:")
                        for f in patch["files"]:
                            logger.info(f"  {f.get('action', 'modify')}: {f['path']}")
                        logger.info(f"  説明: {patch.get('explanation', '')[:300]}")
                        logger.info("=" * 60)
                        answer = input("[APPROVE] 適用しますか? (y/n): ").strip().lower()
                        if answer != "y":
                            logger.info("[APPROVE] ユーザーが拒否。スキップ。")
                            state["execution_result"] = {"success": False, "error": "user_rejected"}
                            state["last_completed_phase"] = "EXECUTE"
                            sm.save(state)
                            modified_paths = []
                            resume_phase = "EXECUTE"
                            state["verification_result"] = {"success": False, "skipped": True}
                            state["last_completed_phase"] = "VERIFY"
                            sm.save(state)

                    # パッチ適用
                    modified_paths = apply_patch(patch, workspace)
                    logger.info(f"[EXECUTE] パッチ適用完了: {[str(p.relative_to(workspace)) for p in modified_paths]}")

                    # diff行数チェック
                    diff_lines = compute_patch_diff_lines(patch, backup_map)
                    logger.info(f"[EXECUTE] diff行数: {diff_lines}")
                    if diff_lines > MAX_DIFF_LINES:
                        logger.warning(f"[EXECUTE] diff行数 {diff_lines} > 上限 {MAX_DIFF_LINES}。ロールバックします。")
                        rollback_with_backup(modified_paths, backup_map, workspace)
                        modified_paths = []
                        state["execution_result"] = {
                            "success": False,
                            "error": f"diff行数超過: {diff_lines} > {MAX_DIFF_LINES}",
                            "patch_explanation": patch.get("explanation", ""),
                        }
                    else:
                        state["execution_result"] = {
                            "success": True,
                            "files_modified": len(modified_paths),
                            "diff_lines": diff_lines,
                            "patch_explanation": patch.get("explanation", ""),
                            "git_available": preflight["git_available"],
                            "modified_files": [
                                str(p.relative_to(workspace)).replace("\\", "/")
                                for p in modified_paths
                            ],
                            "backup_dir": str(
                                bak_dir.relative_to(output_dir)
                            ).replace("\\", "/"),
                        }
                except RuntimeError as e:
                    logger.error(f"[EXECUTE] エラー: {e}")
                    if modified_paths:
                        rollback_with_backup(modified_paths, backup_map, workspace)
                        modified_paths = []
                    state["execution_result"] = {"success": False, "error": str(e)}
                except Exception as e:
                    logger.error(f"[EXECUTE] 予期しないエラー: {e}")
                    if modified_paths:
                        rollback_with_backup(modified_paths, backup_map, workspace)
                        modified_paths = []
                    state["execution_result"] = {"success": False, "error": str(e)}

        state["last_completed_phase"] = "EXECUTE"
        sm.save(state)
    else:
        logger.info("[EXECUTE] resume: スキップ（完了済み）")

    # ── VERIFY ──
    if not (resume_phase and _should_skip_phase(resume_phase, "VERIFY")):
        state["phase"] = "VERIFY"
        exec_result = state.get("execution_result", {})

        if not modified_paths and exec_result.get("modified_files"):
            modified_paths, backup_map = restore_rollback_context(
                state, workspace, output_dir,
            )
            if modified_paths:
                logger.info(f"[VERIFY] ロールバックコンテキストをstateから復元 ({len(modified_paths)}ファイル)")

        if args.dry_run:
            logger.info("[VERIFY] dry-runモード: スキップ")
            state["verification_result"] = {"dry_run": True, "skipped": True}
        elif not exec_result.get("success", False):
            logger.info("[VERIFY] EXECUTE失敗のためスキップ")
            state["verification_result"] = {"skipped": True, "reason": "execute_failed"}
        else:
            logger.info("[VERIFY] 検証コマンドを実行中...")
            verifier = Verifier(workspace)
            verify_result = verifier.verify(selected)
            state["verification_result"] = verify_result
            if verify_result["success"]:
                logger.info(f"[VERIFY] ✅ 検証成功 (exit_code={verify_result['exit_code']})")
                exec_git = state.get("execution_result", {}).get("git_available", False)
                if getattr(args, "auto_commit", False) and exec_git:
                    try:
                        subprocess.run(
                            ["git", "add", "-A"],
                            cwd=str(workspace),
                            capture_output=True, text=True, timeout=10,
                        )
                        task_id = selected.get("task_id", "unknown") if selected else "unknown"
                        subprocess.run(
                            ["git", "commit", "-m", f"[AGI-Kernel] auto-fix: {task_id}"],
                            cwd=str(workspace),
                            capture_output=True, text=True, timeout=10,
                        )
                        logger.info("[VERIFY] 🔒 auto-commit 完了")
                        state["verification_result"]["auto_committed"] = True
                    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as ce:
                        logger.warning(f"[VERIFY] ⚠️ auto-commit 失敗: {ce}")
                elif exec_git:
                    logger.info("[VERIFY] ⚠️ VERIFY成功。次サイクル安定化のため手動commitを推奨します。")
            else:
                logger.warning(f"[VERIFY] ❌ 検証失敗 (exit_code={verify_result['exit_code']})")
                logger.warning(f"[VERIFY] 出力: {verify_result['output'][:500]}")
                if modified_paths:
                    logger.info("[VERIFY] 変更をロールバックします...")
                    rollback_with_backup(modified_paths, backup_map, workspace)
                    state["verification_result"]["rolled_back"] = True

        state["last_completed_phase"] = "VERIFY"
        sm.save(state)
    else:
        logger.info("[VERIFY] resume: スキップ（完了済み）")

    # ── LEARN ──
    paused_now = False
    if not (resume_phase and _should_skip_phase(resume_phase, "LEARN")):
        state["phase"] = "LEARN"
        category = ""
        error_msg = ""
        exec_result = state.get("execution_result", {})
        verify_result = state.get("verification_result", {})
        if args.dry_run:
            outcome = "PARTIAL"
            note = "dry_run"
        elif verify_result.get("success", False):
            outcome = "SUCCESS"
            note = "auto_fix_verified"
        elif exec_result.get("success", False) and not verify_result.get("success", False):
            outcome = "FAILURE"
            note = "verify_failed"
            error_msg = verify_result.get("output", "verification failed")[:500]
            category = classify_failure(error_msg)
            paused_now = record_failure(state, selected["task_id"], category, error_msg)
        else:
            outcome = "FAILURE"
            note = "execute_failed"
            error_msg = exec_result.get("error", "execute failed")[:500]
            category = classify_failure(error_msg)
            paused_now = record_failure(state, selected["task_id"], category, error_msg)

        record_ki(
            outcome=outcome,
            cycle_id=state["cycle_id"],
            task_id=selected["task_id"] if selected else "none",
            note=note,
            metadata={
                "failure_class": category if outcome == "FAILURE" else None,
                "error_summary": (error_msg[:200] if outcome == "FAILURE" else None),
                "verification_success": verify_result.get("success", None),
                "files_modified": exec_result.get("files_modified", 0),
            },
        )
        logger.info(f"[LEARN] KI Learning記録: outcome={outcome}, note={note}")
        state["last_completed_phase"] = "LEARN"
        sm.save(state)
    else:
        logger.info("[LEARN] resume: スキップ（完了済み）")
        outcome = state.get("execution_result", {}).get("success", False) and \
                  state.get("verification_result", {}).get("success", False)
        outcome = "SUCCESS" if outcome else "PARTIAL"
        if selected and selected.get("task_id") in state.get("paused_tasks", []):
            paused_now = True

    # ── CHECKPOINT ──
    state["phase"] = "CHECKPOINT"
    state["last_completed_phase"] = "CHECKPOINT"
    state["completed_at"] = datetime.now(JST).isoformat()

    paused_now_flag = paused_now
    if paused_now_flag:
        state["status"] = "PAUSED"
        logger.warning(f"[CHECKPOINT] ⚠️ タスク {selected['task_id']} が {MAX_TASK_FAILURES}回失敗 → PAUSED停止")
    else:
        state["status"] = "COMPLETED"
    sm.save(state)

    # レポート出力
    blocked = [c for c in candidates if not c.get("auto_fixable", True)]
    report = {
        "cycle_id": state["cycle_id"],
        "status": state["status"],
        "scan_summary": {
            "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
            "pytest_errors": state["scan_results"].get("pytest_errors", 0),
            "pytest_failures": state["scan_results"].get("pytest_failures", 0),
        },
        "candidates_count": len(candidates),
        "blocked_candidates": [
            {"task_id": c["task_id"], "title": c["title"], "blocked_reason": c.get("blocked_reason", "")}
            for c in blocked
        ],
        "selected_task": selected,
        "outcome": outcome,
        "paused_tasks": state.get("paused_tasks", []),
        "token_usage": state.get("token_usage", {}),
    }
    sm.save_report(report, date_str, state["cycle_id"])
    logger.info(f"[CHECKPOINT] state保存完了: {sm.state_path}")

    # Webhook通知 (v0.6.1: 堅牢化版)
    webhook_url = getattr(args, "webhook_url", None)
    if webhook_url:
        send_webhook(webhook_url, {
            "summary": f"AGI Kernel: cycle={state['cycle_id']} status={state['status']} outcome={outcome}",
            "cycle_id": state["cycle_id"],
            "status": state["status"],
            "outcome": outcome,
            "token_usage": state.get("token_usage", {}),
        }, cycle_id=state["cycle_id"])

    return EXIT_PAUSED if paused_now_flag else EXIT_SUCCESS


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AGI Kernel — 自己改善ループ (v0.6.3)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="1サイクルのみ実行して終了（デフォルト動作）",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="常駐モード: --interval 秒ごとにサイクルを繰り返す",
    )
    parser.add_argument(
        "--interval", type=int, default=300,
        help="--loop 時のサイクル間隔（秒、デフォルト: 300）",
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
        "--auto-commit", action="store_true", dest="auto_commit",
        help="VERIFY成功時に自動commitする（デフォルトOFF）",
    )
    parser.add_argument(
        "--approve", action="store_true",
        help="パッチ適用前に人間の承認を要求する",
    )
    parser.add_argument(
        "--workspace", type=str, default=str(_DEFAULT_WORKSPACE),
        help=f"ワークスペースルート（デフォルト: {_DEFAULT_WORKSPACE}）",
    )
    # P3-a: マルチリポ対応
    parser.add_argument(
        "--workspaces", type=str, nargs="+", default=None,
        help="複数ワークスペースを巡回（例: --workspaces /repo1 /repo2）",
    )
    parser.add_argument(
        "--llm-model", type=str, default=None, dest="llm_model",
        help="LLMモデル名（デフォルト: gemini-2.5-flash / env AGI_KERNEL_LLM_MODEL）",
    )
    parser.add_argument(
        "--llm-strong-model", type=str, default=None, dest="llm_strong_model",
        help="強力LLMモデル（デフォルト: gemini-2.5-pro / env AGI_KERNEL_LLM_STRONG_MODEL）",
    )
    parser.add_argument(
        "--webhook-url", type=str, default=None, dest="webhook_url",
        help="サイクル完了/PAUSED時にWebhook通知を送るURL（Discord/Slack互換）",
    )
    parser.add_argument(
        "--lint-severity", type=str, default="error", dest="lint_severity",
        help="workflow_lint取得レベル（カンマ区切り: error,caution,advisory,warning）",
    )
    parser.add_argument(
        "--log-json", action="store_true", dest="log_json",
        help="ログ出力をJSON構造化形式にする",
    )
    return parser


def main() -> int:
    """エントリーポイント。"""
    parser = build_parser()
    args = parser.parse_args()

    # ロギング初期化
    _setup_logging(json_mode=getattr(args, "log_json", False))

    # P3-a: マルチリポ対応
    workspaces: list[Path] = []
    if args.workspaces:
        workspaces = [Path(w).resolve() for w in args.workspaces]
    else:
        workspaces = [Path(args.workspace).resolve()]

    if args.loop:
        # 常駐モード
        logger.info(f"[KERNEL] 常駐モード開始 (interval={args.interval}s, workspaces={len(workspaces)})")
        cycle_count = 0
        try:
            while True:
                cycle_count += 1
                for ws_idx, ws in enumerate(workspaces):
                    ws_label = f"[{ws_idx+1}/{len(workspaces)}] {ws.name}" if len(workspaces) > 1 else ""
                    logger.info(f"[KERNEL] === サイクル #{cycle_count} 開始 {ws_label}===")
                    exit_code = run_cycle(args, workspace=ws)
                    if exit_code != 0:
                        logger.warning(f"[KERNEL] サイクル #{cycle_count} {ws_label} が exit_code={exit_code} で終了。")
                        if len(workspaces) == 1:
                            return exit_code
                        # マルチリポ: 1つ失敗しても次へ進む
                        continue
                    logger.info(f"[KERNEL] サイクル #{cycle_count} {ws_label} 完了。")
                logger.info(f"[KERNEL] 全ワークスペース完了。{args.interval}秒後に次のサイクル...")
                args.resume = False
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info(f"[KERNEL] Ctrl+C を受信。{cycle_count}サイクル実行後に終了。")
            return 0
    else:
        # 単発モード: 全ワークスペースを巡回
        final_exit = 0
        for ws_idx, ws in enumerate(workspaces):
            if len(workspaces) > 1:
                logger.info(f"[KERNEL] ワークスペース [{ws_idx+1}/{len(workspaces)}]: {ws}")
            exit_code = run_cycle(args, workspace=ws)
            if exit_code != 0:
                final_exit = exit_code
                if len(workspaces) == 1:
                    return exit_code
        return final_exit


if __name__ == "__main__":
    if _HAS_LOGGER:
        exit_code = run_logged_main(
            agent="agi_kernel",
            workflow="agi_kernel",
            main_func=main,
            phase_name="AGI_KERNEL_CYCLE",
        )
        raise SystemExit(exit_code)
    else:
        raise SystemExit(main())
