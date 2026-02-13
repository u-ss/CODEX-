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
import re

__version__ = "0.2.0"

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
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
# FileLock — 多重起動防止
# ============================================================

# ロックのTTL（秒）。この時間を超えた stale lock は回収する
LOCK_TTL_SECONDS = 600  # 10分


class FileLock:
    """Cross-platform なファイルベースのロック。

    lockファイルを排他的に作成することで多重起動を防ぐ。
    TTL超過のstale lockは自動回収する。
    """

    def __init__(self, lock_path: Path, ttl: int = LOCK_TTL_SECONDS):
        self.lock_path = lock_path
        self.ttl = ttl
        self._acquired = False

    def acquire(self) -> bool:
        """ロックを取得する。成功ならTrue。"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # stale lock を回収
        if self.lock_path.exists():
            try:
                content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                created_at = content.get("created_at", 0)
                if (time.time() - created_at) > self.ttl:
                    print(f"[LOCK] stale lock を回収 (age={time.time() - created_at:.0f}s > TTL={self.ttl}s)")
                    self.lock_path.unlink(missing_ok=True)
                else:
                    # 有効なロックが存在
                    return False
            except (json.JSONDecodeError, OSError, ValueError):
                # ロックファイルが壊れている → 回収
                self.lock_path.unlink(missing_ok=True)

        # O_CREAT|O_EXCL で排他作成（cross-platform）
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                lock_data = json.dumps({
                    "pid": os.getpid(),
                    "created_at": time.time(),
                    "created_iso": datetime.now(JST).isoformat(),
                }).encode("utf-8")
                os.write(fd, lock_data)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._acquired = True
            return True
        except FileExistsError:
            # 別プロセスが先にロックを取得
            return False

    def release(self) -> None:
        """ロックを解放する。"""
        if self._acquired:
            self.lock_path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


# ============================================================
# StateManager — state.json の保存/読込/再開
# ============================================================

class StateManager:
    """AGI Kernelの状態を管理する。

    v0.2.0: atomic write + backup + .bak fallback
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.state_path = output_dir / "state.json"
        self._bak_path = output_dir / "state.json.bak"
        self._tmp_path = output_dir / "state.json.tmp"

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
        """state.jsonを読み込む。壊れていれば.bakをフォールバック。"""
        # まず state.json を試す
        data = self._try_load(self.state_path)
        if data is not None:
            return data
        # state.json が無い/壊れている → .bak を試す
        data = self._try_load(self._bak_path)
        if data is not None:
            print("[StateManager] state.jsonが破損。state.json.bakから復旧しました。")
        return data

    @staticmethod
    def _try_load(path: Path) -> Optional[dict[str, Any]]:
        """指定パスのJSONを読み込む。失敗ならNone。"""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, state: dict[str, Any]) -> None:
        """state.jsonをatomic writeで保存する。

        手順:
        1. 既存 state.json → state.json.bak にコピー
        2. state.json.tmp に書き込み + fsync
        3. os.replace で state.json に置換（atomic）
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1. バックアップ
        if self.state_path.exists():
            try:
                shutil.copy2(str(self.state_path), str(self._bak_path))
            except OSError:
                pass  # バックアップ失敗は致命的でない

        # 2. tmp に書き込み + fsync
        with open(self._tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # 3. atomic replace
        os.replace(str(self._tmp_path), str(self.state_path))

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
# pytest出力パーサー（pure function — テスト可能）
# ============================================================

# errors_count 抽出用パターン
_RE_ERRORS_IN = re.compile(r"(?:^|\s)(\d+)\s+errors?\s+in\s", re.MULTILINE)
_RE_INTERRUPTED = re.compile(r"Interrupted:\s+(\d+)\s+errors?\s+during\s+collection")

# headline 候補抽出用キーワード
_EXCEPTION_NAMES = (
    "ModuleNotFoundError", "ImportError", "ConnectionRefusedError",
    "FileNotFoundError", "SyntaxError", "AttributeError",
    "TypeError", "NameError", "OSError", "PermissionError",
)


def _extract_headline(lines: list[str]) -> str:
    """出力行からエラー原因を示す最適な1行を抽出する。"""
    # 優先度1: "ERROR collecting" を含む行
    for line in lines:
        if "ERROR collecting" in line:
            return line.strip()

    # 優先度2: 行頭が "E   " の例外行（pytestの詳細出力）
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("E   "):
            return stripped

    # 優先度3: 既知の例外名を含む行
    for line in lines:
        for exc in _EXCEPTION_NAMES:
            if exc in line:
                return line.strip()

    # 優先度4: "Interrupted: N errors during collection"
    for line in lines:
        if "Interrupted:" in line and "errors" in line:
            return line.strip()

    # フォールバック: 末尾行
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


def parse_pytest_result(output: str, exit_code: int) -> dict[str, Any]:
    """pytest出力とexit_codeからスキャン結果dictを生成する。

    Pure function。subprocess を呼ばずにユニットテスト可能。

    Args:
        output: stdout + stderr の結合文字列
        exit_code: プロセスの returncode

    Returns:
        dict with keys:
            available, failures, exit_code, summary, tail（後方互換）
            headline, errors_count, error_lines（v0.2.1追加）
    """
    lines = output.strip().splitlines() if output.strip() else []
    summary = lines[-1] if lines else ""
    tail = lines[-20:] if lines else []  # デバッグ用に末尾20行

    # "N failed" パターンをパース
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

    # errors_count: "N errors in ..." または "Interrupted: N errors"
    errors_count = 0
    full_text = output if output else ""
    m = _RE_ERRORS_IN.search(full_text)
    if m:
        errors_count = int(m.group(1))
    else:
        m = _RE_INTERRUPTED.search(full_text)
        if m:
            errors_count = int(m.group(1))

    # exit_code!=0 なのに failures==0 → 収集エラー等。最低1を保証
    if exit_code not in (0, None) and exit_code != -1 and failures == 0:
        failures = 1

    # headline: エラー原因が分かる1行
    headline = _extract_headline(lines) if lines else ""

    # error_lines: ERROR/E行を最大10行
    error_lines = _extract_error_lines(lines)

    return {
        "available": True,
        "failures": failures,
        "exit_code": exit_code,
        "summary": summary,
        "tail": tail,
        # v0.2.1 追加
        "headline": headline,
        "errors_count": errors_count,
        "error_lines": error_lines,
    }


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
        parts.extend(error_lines[:5])  # 最大5行

    desc = "\n".join(parts)
    return desc[:800]  # 長すぎ防止


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
    # pytest の失敗 / 異常終了 → 優先度2
    pytest_data = scan_results.get("pytest", {})
    pytest_exit = pytest_data.get("exit_code", 0)
    pytest_failures = pytest_data.get("failures", 0)
    errors_count = pytest_data.get("errors_count", 0)
    description = _build_pytest_description(pytest_data)

    if pytest_failures > 0:
        # タイトル: errors_count があればそちらを優先表示
        if errors_count > 0:
            title = f"収集エラー修正 ({errors_count}件)"
        else:
            title = f"テスト失敗修正 ({pytest_failures}件)"
        candidates.append({
            "task_id": f"pytest_exit_{pytest_exit}",
            "source": "pytest",
            "priority": 2,
            "title": title,
            "description": description,
            "estimated_effort": "medium",
        })
    elif pytest_exit not in (0, None) and pytest_exit != -1:
        title = f"pytest異常終了 (exit_code={pytest_exit})"
        candidates.append({
            "task_id": f"pytest_exit_{pytest_exit}",
            "source": "pytest",
            "priority": 2,
            "title": title,
            "description": description,
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
# フェーズ順序定義（--resume 用）
# ============================================================

PHASE_ORDER = ["BOOT", "SCAN", "SENSE", "SELECT", "EXECUTE", "VERIFY", "LEARN", "CHECKPOINT"]


def _should_skip_phase(current_phase: str, target_phase: str) -> bool:
    """resume時、target_phaseが既に完了済みかを判定する。

    current_phaseは「最後に完了したphase」ではなく「最後に開始したphase」。
    よってcurrent_phase自体はやり直す（安全側）。
    """
    try:
        current_idx = PHASE_ORDER.index(current_phase)
        target_idx = PHASE_ORDER.index(target_phase)
        # target が current より前 → スキップ
        return target_idx < current_idx
    except ValueError:
        return False


# ============================================================
# メインサイクル
# ============================================================

def run_cycle(args: argparse.Namespace) -> int:
    """1サイクルを実行する。"""
    workspace = Path(args.workspace).resolve()
    output_dir = workspace / "_outputs" / "agi_kernel"
    sm = StateManager(output_dir)

    # ── LOCK ──
    lock = FileLock(output_dir / "lock")
    if not lock.acquire():
        print("[LOCK] 別のAGI Kernelプロセスが実行中です。終了します。")
        return 2

    try:
        return _run_cycle_inner(args, workspace, output_dir, sm)
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
                # RUNNING → phaseを尊重して再開
                resume_phase = state.get("phase", "BOOT")
                print(f"[BOOT] phase={resume_phase} からやり直します。")
    else:
        state = sm.new_state()

    state["phase"] = "BOOT"
    state["status"] = "RUNNING"
    date_str = datetime.now(JST).strftime("%Y%m%d")

    print(f"[BOOT] サイクル開始: cycle_id={state['cycle_id']}")
    sm.save(state)  # BOOT checkpoint

    # ── SCAN ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SCAN")):
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
        sm.save(state)  # SCAN checkpoint
    else:
        print("[SCAN] resume: スキップ（完了済み）")

    # ── SENSE ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SENSE")):
        state["phase"] = "SENSE"
        candidates = generate_candidates(state["scan_results"])
        state["candidates"] = candidates
        sm.save_candidates(candidates, date_str)
        print(f"[SENSE] タスク候補: {len(candidates)}件")
        sm.save(state)  # SENSE checkpoint
    else:
        print("[SENSE] resume: スキップ（完了済み）")
        candidates = state.get("candidates", [])

    # ── SELECT ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SELECT")):
        state["phase"] = "SELECT"
        selected = select_task(candidates, state.get("paused_tasks", []))
        state["selected_task"] = selected
        if selected is None:
            print("[SELECT] 対処可能なタスクがありません。サイクル完了。")
            state["status"] = "COMPLETED"
            state["completed_at"] = datetime.now(JST).isoformat()
            state["phase"] = "CHECKPOINT"
            sm.save(state)
            # 候補なし早期終了でも report.json を出力
            report = {
                "cycle_id": state["cycle_id"],
                "status": state["status"],
                "reason": "no_candidates",
                "scan_summary": {
                    "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
                    "pytest_failures": state["scan_results"].get("pytest_failures", 0),
                },
                "candidates_count": 0,
                "selected_task": None,
                "outcome": "SUCCESS",
                "paused_tasks": state.get("paused_tasks", []),
            }
            report_path = sm.save_report(report, date_str)
            _record_ki("SUCCESS", cycle_id=state["cycle_id"], task_id="none", note="no_candidates")
            print(f"[CHECKPOINT] state保存完了: {sm.state_path}")
            print(f"[CHECKPOINT] レポート出力: {report_path}")
            return 0
        print(f"[SELECT] タスク選択: {selected['task_id']} — {selected['title']}")
        sm.save(state)  # SELECT checkpoint
    else:
        print("[SELECT] resume: スキップ（完了済み）")
        selected = state.get("selected_task")

    # ── EXECUTE ──
    if not (resume_phase and _should_skip_phase(resume_phase, "EXECUTE")):
        state["phase"] = "EXECUTE"
        if args.dry_run:
            print("[EXECUTE] dry-runモード: スキップ")
            state["execution_result"] = {"dry_run": True, "skipped": True}
        else:
            # MVP: 実行はまだ未実装（将来拡張ポイント）
            print("[EXECUTE] MVP: 自動実行は未実装。タスクレポートのみ出力します。")
            state["execution_result"] = {"implemented": False, "note": "MVP — 手動対応が必要"}
        sm.save(state)  # EXECUTE checkpoint
    else:
        print("[EXECUTE] resume: スキップ（完了済み）")

    # ── VERIFY ──
    if not (resume_phase and _should_skip_phase(resume_phase, "VERIFY")):
        state["phase"] = "VERIFY"
        if args.dry_run:
            print("[VERIFY] dry-runモード: スキップ")
            state["verification_result"] = {"dry_run": True, "skipped": True}
        else:
            print("[VERIFY] MVP: 自動検証は未実装。")
            state["verification_result"] = {"implemented": False}
        sm.save(state)  # VERIFY checkpoint
    else:
        print("[VERIFY] resume: スキップ（完了済み）")

    # ── LEARN ──
    if not (resume_phase and _should_skip_phase(resume_phase, "LEARN")):
        state["phase"] = "LEARN"
        outcome = "SUCCESS" if args.dry_run else "PARTIAL"
        _record_ki(
            outcome=outcome,
            cycle_id=state["cycle_id"],
            task_id=selected["task_id"] if selected else "none",
            note="dry_run" if args.dry_run else "mvp_partial",
        )
        print(f"[LEARN] KI Learning記録: outcome={outcome}")
        sm.save(state)  # LEARN checkpoint
    else:
        print("[LEARN] resume: スキップ（完了済み）")
        outcome = "SUCCESS" if args.dry_run else "PARTIAL"

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
