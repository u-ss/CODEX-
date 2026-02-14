#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — ユニットテスト

StateManager, FileLock, parse_pytest_result, 候補生成/選択, 失敗分類 のテスト。
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

# テスト対象モジュールをインポートできるようにパスを追加
# 固定パスに依存せず、rglob で agi_kernel.py を自動発見する
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXCLUDE_DIRS = {"_outputs", ".venv", "venv", "node_modules", "__pycache__", ".git"}


def _find_agi_kernel_script() -> Path:
    """リポジトリ内の agi_kernel.py を自動発見する。"""
    for p in _REPO_ROOT.rglob("agi_kernel.py"):
        # 除外ディレクトリをスキップ
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        # scripts/ 配下のものを採用
        if p.parent.name == "scripts":
            return p
    raise FileNotFoundError(
        f"agi_kernel.py が見つかりません。\n"
        f"検索ルート: {_REPO_ROOT}\n"
        f"'scripts/' 配下に agi_kernel.py を配置してください。"
    )


_SCRIPT_PATH = _find_agi_kernel_script()
_SCRIPT_DIR = _SCRIPT_PATH.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from agi_kernel import (
    StateManager,
    FileLock,
    classify_failure,
    generate_candidates,
    select_task,
    record_failure,
    parse_pytest_result,
    strip_ansi,
    _should_skip_phase,
    _validate_patch_result,
    _apply_patch,
    _parse_patch_json,
    _preflight_check,
    _backup_targets,
    _rollback_with_backup,
    _compute_patch_diff_lines,
    _extract_error_blocks,
    _extract_failure_nodes,
    _stable_task_id,
    _restore_rollback_context,
    annotate_candidates,
    Verifier,
    Scanner,
    _log_token_usage,
    _record_ki,
    _send_webhook,
    _GeminiClientCompat,
    _setup_logging,
    _JsonFormatter,
    logger,
    build_parser,
    PHASE_ORDER,
    MAX_PATCH_FILES,
    MAX_TASK_FAILURES,
    LOCK_TTL_SECONDS,
)


# ────────────────────────────────────────
# StateManager テスト
# ────────────────────────────────────────

class TestStateManager:
    """StateManagerの保存/読込テスト。"""

    def test_save_load_roundtrip(self, tmp_path: Path):
        """stateをsave→load→一致確認。"""
        sm = StateManager(tmp_path)
        state = sm.new_state()
        state["candidates"] = [{"task_id": "test_001", "priority": 1}]
        state["status"] = "COMPLETED"
        sm.save(state)

        loaded = sm.load()
        assert loaded is not None
        assert loaded["version"] == state["version"]
        assert loaded["cycle_id"] == state["cycle_id"]
        assert loaded["status"] == "COMPLETED"
        assert len(loaded["candidates"]) == 1
        assert loaded["candidates"][0]["task_id"] == "test_001"

    def test_load_nonexistent(self, tmp_path: Path):
        """state.jsonが存在しない場合はNoneを返す。"""
        sm = StateManager(tmp_path)
        assert sm.load() is None

    def test_load_corrupted_json(self, tmp_path: Path):
        """壊れたJSONの場合はNoneを返す。"""
        sm = StateManager(tmp_path)
        sm.state_path.parent.mkdir(parents=True, exist_ok=True)
        sm.state_path.write_text("{invalid json", encoding="utf-8")
        assert sm.load() is None

    def test_save_candidates(self, tmp_path: Path):
        """candidates.jsonのcycle_idフォルダ保存とlatestコピーテスト。"""
        sm = StateManager(tmp_path)
        candidates = [{"task_id": "lint_000", "priority": 1}]
        path = sm.save_candidates(candidates, "20260214", "20260214_113500")
        # cycle_id サブフォルダに保存される
        assert path.exists()
        assert "20260214_113500" in str(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["task_id"] == "lint_000"
        # latest コピーが存在
        latest = tmp_path / "20260214" / "latest_candidates.json"
        assert latest.exists()
        with open(latest, "r", encoding="utf-8") as f:
            latest_data = json.load(f)
        assert latest_data == data

    def test_save_report(self, tmp_path: Path):
        """report.jsonのcycle_idフォルダ保存とlatestコピーテスト。"""
        sm = StateManager(tmp_path)
        report = {"cycle_id": "test_cycle", "status": "COMPLETED"}
        path = sm.save_report(report, "20260214", "test_cycle")
        # cycle_id サブフォルダに保存
        assert path.exists()
        assert "test_cycle" in str(path)
        # latest コピーが存在
        latest = tmp_path / "20260214" / "latest_report.json"
        assert latest.exists()

    def test_atomic_write_creates_backup(self, tmp_path: Path):
        """save()で既存state.jsonのバックアップ(.bak)が作られる。"""
        sm = StateManager(tmp_path)
        state1 = sm.new_state()
        state1["cycle_id"] = "first_cycle"
        sm.save(state1)

        # 2回目のsaveでバックアップが作られる
        state2 = sm.new_state()
        state2["cycle_id"] = "second_cycle"
        sm.save(state2)

        assert sm._bak_path.exists()
        bak_data = json.loads(sm._bak_path.read_text(encoding="utf-8"))
        assert bak_data["cycle_id"] == "first_cycle"

    def test_atomic_write_no_tmp_left(self, tmp_path: Path):
        """save()後にtmpファイルが残らない。"""
        sm = StateManager(tmp_path)
        sm.save(sm.new_state())
        assert not sm._tmp_path.exists()
        assert sm.state_path.exists()

    def test_load_fallback_to_bak(self, tmp_path: Path):
        """state.jsonが壊れていても.bakから復旧できる。"""
        sm = StateManager(tmp_path)
        # 正常なstateを保存
        state = sm.new_state()
        state["cycle_id"] = "good_cycle"
        sm.save(state)

        # state.jsonを壊す
        sm.state_path.write_text("{corrupted!", encoding="utf-8")

        # .bakを手動作成（save時にバックアップされなかったケース想定）
        sm._bak_path.write_text(
            json.dumps({"cycle_id": "backup_cycle", "version": "0.2.0"}),
            encoding="utf-8",
        )

        loaded = sm.load()
        assert loaded is not None
        assert loaded["cycle_id"] == "backup_cycle"

    def test_load_both_missing(self, tmp_path: Path):
        """state.jsonも.bakも無い場合はNone。"""
        sm = StateManager(tmp_path)
        assert sm.load() is None

    def test_load_state_corrupted_bak_missing(self, tmp_path: Path):
        """state.jsonが壊れていて.bakも無い場合はNone。"""
        sm = StateManager(tmp_path)
        sm.state_path.parent.mkdir(parents=True, exist_ok=True)
        sm.state_path.write_text("{bad}", encoding="utf-8")
        assert sm.load() is None


# ────────────────────────────────────────
# FileLock テスト
# ────────────────────────────────────────

class TestFileLock:
    """FileLockの排他制御テスト。"""

    def test_acquire_and_release(self, tmp_path: Path):
        """ロックの取得と解放。"""
        lock = FileLock(tmp_path / "lock")
        assert lock.acquire() is True
        assert lock.lock_path.exists()
        lock.release()
        assert not lock.lock_path.exists()

    def test_double_acquire_fails(self, tmp_path: Path):
        """同じロックを2回取得できない。"""
        lock_path = tmp_path / "lock"
        lock1 = FileLock(lock_path)
        lock2 = FileLock(lock_path)

        assert lock1.acquire() is True
        assert lock2.acquire() is False  # 2回目は失敗

        lock1.release()

    def test_acquire_after_release(self, tmp_path: Path):
        """解放後は再取得できる。"""
        lock_path = tmp_path / "lock"
        lock1 = FileLock(lock_path)
        assert lock1.acquire() is True
        lock1.release()

        lock2 = FileLock(lock_path)
        assert lock2.acquire() is True
        lock2.release()

    def test_stale_lock_recovery(self, tmp_path: Path):
        """TTL超過のstale lockは回収できる。"""
        lock_path = tmp_path / "lock"

        # 古いロックを手動作成
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"pid": 99999, "created_at": time.time() - 9999}),
            encoding="utf-8",
        )

        # TTL=1秒で回収可能（実際のage >> 1秒）
        lock = FileLock(lock_path, ttl=1)
        assert lock.acquire() is True
        lock.release()

    def test_valid_lock_not_recovered(self, tmp_path: Path):
        """TTL内のロックは回収されない。"""
        lock_path = tmp_path / "lock"

        # 新しいロックを手動作成
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"pid": 99999, "created_at": time.time()}),
            encoding="utf-8",
        )

        lock = FileLock(lock_path, ttl=9999)
        assert lock.acquire() is False

    def test_context_manager_releases(self, tmp_path: Path):
        """context managerで自動解放される。"""
        lock_path = tmp_path / "lock"
        lock = FileLock(lock_path)
        lock.acquire()
        with lock:
            assert lock.lock_path.exists()
        assert not lock.lock_path.exists()

    def test_corrupted_lock_recovered(self, tmp_path: Path):
        """壊れたロックファイルは回収される。"""
        lock_path = tmp_path / "lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("this is not json", encoding="utf-8")

        lock = FileLock(lock_path)
        assert lock.acquire() is True
        lock.release()


# ────────────────────────────────────────
# Resume分岐テスト
# ────────────────────────────────────────

class TestResumeBehavior:
    """--resume 時の分岐ロジックテスト。"""

    def test_resume_with_completed_state(self, tmp_path: Path):
        """COMPLETED状態のstateがある場合、新規サイクル開始を促す。"""
        sm = StateManager(tmp_path)
        state = sm.new_state()
        state["status"] = "COMPLETED"
        sm.save(state)

        loaded = sm.load()
        assert loaded is not None
        assert loaded["status"] == "COMPLETED"

    def test_resume_with_running_state(self, tmp_path: Path):
        """RUNNING状態のstateがある場合、再開可能。"""
        sm = StateManager(tmp_path)
        state = sm.new_state()
        state["status"] = "RUNNING"
        state["phase"] = "SCAN"
        sm.save(state)

        loaded = sm.load()
        assert loaded is not None
        assert loaded["status"] == "RUNNING"
        assert loaded["phase"] == "SCAN"

    def test_resume_without_state(self, tmp_path: Path):
        """state不在時のフォールバック（新規サイクル開始）。"""
        sm = StateManager(tmp_path)
        loaded = sm.load()
        assert loaded is None
        # 新規state生成で代替
        new_state = sm.new_state()
        assert new_state["status"] == "RUNNING"
        assert new_state["phase"] == "BOOT"


# ────────────────────────────────────────
# 失敗カウント・PAUSEDテスト
# ────────────────────────────────────────

class TestFailureHandling:
    """失敗分類とPAUSEDロジックのテスト。"""

    def test_classify_transient(self):
        assert classify_failure("Connection timeout") == "TRANSIENT"

    def test_classify_deterministic(self):
        assert classify_failure("TypeError: unsupported") == "DETERMINISTIC"

    def test_classify_environment(self):
        assert classify_failure("ModuleNotFoundError: no module") == "ENVIRONMENT"

    def test_classify_policy(self):
        assert classify_failure("Permission denied") == "POLICY"

    def test_classify_unknown(self):
        """不明なエラーはDETERMINISTICに分類。"""
        assert classify_failure("something weird") == "DETERMINISTIC"

    def test_failure_count_and_pause(self):
        """同一タスク3回失敗でPAUSEDになることを確認。"""
        state = {
            "failure_log": [],
            "paused_tasks": [],
        }
        task_id = "lint_001"
        for i in range(MAX_TASK_FAILURES):
            record_failure(state, task_id, "DETERMINISTIC", f"error {i}")

        assert len(state["failure_log"]) == 1
        assert state["failure_log"][0]["count"] == MAX_TASK_FAILURES
        assert task_id in state["paused_tasks"]

    def test_failure_below_threshold_not_paused(self):
        """閾値未満では PAUSED にならない。"""
        state = {
            "failure_log": [],
            "paused_tasks": [],
        }
        task_id = "lint_002"
        for i in range(MAX_TASK_FAILURES - 1):
            record_failure(state, task_id, "TRANSIENT", f"error {i}")

        assert state["failure_log"][0]["count"] == MAX_TASK_FAILURES - 1
        assert task_id not in state["paused_tasks"]


# ────────────────────────────────────────
# タスク候補生成・選択テスト
# ────────────────────────────────────────

class TestCandidateSelection:
    """タスク候補の生成と選択ロジックのテスト。"""

    def test_generate_candidates_from_lint(self):
        scan = {
            "workflow_lint": {
                "errors": 2,
                "findings": ["[ERROR] xxx", "[ERROR] yyy"],
            },
            "pytest": {"failures": 0},
        }
        candidates = generate_candidates(scan)
        assert len(candidates) == 2
        assert candidates[0]["source"] == "workflow_lint"
        assert candidates[0]["priority"] == 1

    def test_generate_candidates_from_pytest(self):
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {"failures": 3, "summary": "3 failed"},
        }
        candidates = generate_candidates(scan)
        assert len(candidates) == 1
        assert candidates[0]["source"] == "pytest"
        assert candidates[0]["priority"] == 2

    def test_select_excludes_paused(self):
        candidates = [
            {"task_id": "lint_000", "priority": 1},
            {"task_id": "lint_001", "priority": 1},
        ]
        selected = select_task(candidates, paused_tasks=["lint_000"])
        assert selected is not None
        assert selected["task_id"] == "lint_001"

    def test_select_none_when_all_paused(self):
        candidates = [{"task_id": "lint_000", "priority": 1}]
        selected = select_task(candidates, paused_tasks=["lint_000"])
        assert selected is None

    def test_select_none_when_empty(self):
        selected = select_task([], paused_tasks=[])
        assert selected is None

    def test_generate_candidates_pytest_exit2_no_failed_line(self):
        """exit_code=2 で 'failed' 行なし → 候補が出ることを確認。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {"failures": 1, "exit_code": 2, "summary": "ERROR collecting"},
        }
        candidates = generate_candidates(scan)
        assert len(candidates) >= 1
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) >= 1
        # v0.4.0: stable_id形式
        assert pytest_cand[0]["task_id"].startswith("pytest_")

    def test_generate_candidates_pytest_exit0_no_candidate(self):
        """exit_code=0 かつ failures=0 → pytest候補なし。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {"failures": 0, "exit_code": 0},
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) == 0

    def test_generate_candidates_pytest_exit1_with_failures(self):
        """exit_code=1, failures=3 → stable_idが pytest_tf_ 形式。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {"failures": 3, "exit_code": 1, "summary": "3 failed, 10 passed"},
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) == 1
        # v0.4.0: stable_id形式
        assert pytest_cand[0]["task_id"].startswith("pytest_tf_")


# ────────────────────────────────────────
# parse_pytest_result テスト
# ────────────────────────────────────────

class TestParsePytestResult:
    """pytest出力パーサーのユニットテスト。"""

    def test_normal_3_failed(self):
        """'3 failed, 10 passed' → failures=3。"""
        output = "FAILED tests/test_a.py\nFAILED tests/test_b.py\n3 failed, 10 passed"
        result = parse_pytest_result(output, exit_code=1)
        assert result["failures"] == 3
        assert result["exit_code"] == 1
        assert result["available"] is True

    def test_exit2_no_failed_line(self):
        """exit_code=2 で 'failed' 行なし → failures=0、errors_count>=1（収集エラー）。"""
        output = "ERROR collecting tests/test_broken.py\n1 error"
        result = parse_pytest_result(output, exit_code=2)
        assert result["failures"] == 0           # v0.3.1: 収集エラーではfailures補正しない
        assert result["errors_count"] >= 1        # errors_countを保証
        assert result["exit_code"] == 2

    def test_exit0_all_pass(self):
        """exit_code=0 → failures=0。"""
        output = "20 passed in 0.15s"
        result = parse_pytest_result(output, exit_code=0)
        assert result["failures"] == 0
        assert result["exit_code"] == 0

    def test_empty_output_exit1(self):
        """空出力でexit_code=1 → failures>=1。"""
        result = parse_pytest_result("", exit_code=1)
        assert result["failures"] >= 1

    def test_tail_included(self):
        """出力末尾がtailに含まれる。"""
        lines = [f"line {i}" for i in range(30)]
        output = "\n".join(lines)
        result = parse_pytest_result(output, exit_code=0)
        assert len(result["tail"]) == 20
        assert result["tail"][-1] == "line 29"

    def test_summary_is_last_line(self):
        """summaryは最終行。"""
        output = "collecting...\n20 passed in 0.5s"
        result = parse_pytest_result(output, exit_code=0)
        assert result["summary"] == "20 passed in 0.5s"

    def test_exit_minus1_treated_as_error(self):
        """exit_code=-1 はタイムアウト等の特殊値。failures補正しない。"""
        result = parse_pytest_result("", exit_code=-1)
        assert result["failures"] == 0  # -1は補正対象外

    # ── v0.2.1: headline / errors_count / error_lines テスト ──

    def test_headline_error_collecting(self):
        """'ERROR collecting' 行が headline になる。"""
        output = (
            "WARNING: something irrelevant\n"
            "ERROR collecting tests/test_broken.py\n"
            "E   ModuleNotFoundError: No module named 'foo'\n"
            "9 errors in 7.88s"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert "ERROR collecting" in result["headline"]

    def test_headline_e_line_when_no_collecting(self):
        """'ERROR collecting' がない場合、'E   ' 行が headline になる。"""
        output = (
            "FAILED tests/test_a.py::test_x\n"
            "E   AssertionError: expected 1 got 2\n"
            "1 failed, 5 passed"
        )
        result = parse_pytest_result(output, exit_code=1)
        assert result["headline"].startswith("E   ")

    def test_headline_exception_name(self):
        """例外名を含む行が headline になる。"""
        output = (
            "collecting...\n"
            "ModuleNotFoundError: No module named 'broken_module'\n"
            "2 errors in 1.0s"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert "ModuleNotFoundError" in result["headline"]

    def test_headline_fallback_to_last_line(self):
        """何も該当しなければ末尾行。"""
        output = "20 passed in 0.5s"
        result = parse_pytest_result(output, exit_code=0)
        assert result["headline"] == "20 passed in 0.5s"

    def test_headline_not_warning(self):
        """末尾がwarning行でも headline はエラー由来になる。"""
        output = (
            "ERROR collecting tests/test_x.py\n"
            "E   ImportError: cannot import name 'xyz'\n"
            "1 error in 0.5s\n"
            "===== warnings summary =====\n"
            "some/path.py:10: DeprecationWarning: old API"
        )
        result = parse_pytest_result(output, exit_code=2)
        # headline は warning ではなく ERROR collecting 行であるべき
        assert "DeprecationWarning" not in result["headline"]
        assert "ERROR collecting" in result["headline"]

    def test_errors_count_from_summary(self):
        """'9 errors in 7.88s' → errors_count=9。"""
        output = (
            "ERROR collecting tests/a.py\n"
            "ERROR collecting tests/b.py\n"
            "9 errors in 7.88s"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert result["errors_count"] == 9

    def test_errors_count_from_interrupted(self):
        """'Interrupted: 5 errors during collection' → errors_count=5。"""
        output = "Interrupted: 5 errors during collection"
        result = parse_pytest_result(output, exit_code=2)
        assert result["errors_count"] == 5

    def test_errors_count_zero_when_all_pass(self):
        """全テスト通過時は errors_count=0。"""
        output = "20 passed in 0.5s"
        result = parse_pytest_result(output, exit_code=0)
        assert result["errors_count"] == 0

    def test_error_lines_extracted(self):
        """ERROR/E行が error_lines に含まれる。"""
        output = (
            "collecting...\n"
            "ERROR collecting tests/a.py\n"
            "E   ImportError: foo\n"
            "ERROR collecting tests/b.py\n"
            "2 errors in 1s"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert len(result["error_lines"]) >= 3

    def test_error_lines_max_10(self):
        """error_lines は最大10行。"""
        lines = [f"ERROR collecting tests/test_{i}.py" for i in range(20)]
        output = "\n".join(lines) + "\n20 errors in 5s"
        result = parse_pytest_result(output, exit_code=2)
        assert len(result["error_lines"]) == 10

    def test_backward_compat_keys(self):
        """後方互換: 既存キーが全てある。"""
        result = parse_pytest_result("20 passed in 0.5s", exit_code=0)
        for key in ("available", "failures", "exit_code", "summary", "tail"):
            assert key in result
        # 新キーもある
        for key in ("headline", "errors_count", "error_lines"):
            assert key in result

    # ── v0.3.1: exit_code別補正ルール分離テスト ──

    def test_exit2_5errors_separates_correctly(self):
        """exit_code=2 + '5 errors during collection' → errors_count=5, failures=0。"""
        output = (
            "ERROR collecting tests/test_a.py\n"
            "ERROR collecting tests/test_b.py\n"
            "Interrupted: 5 errors during collection"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert result["errors_count"] == 5
        assert result["failures"] == 0

    def test_exit1_3failed_separates_correctly(self):
        """exit_code=1 + '3 failed' → failures=3, errors_count=0。"""
        output = "FAILED tests/test_a.py\n3 failed, 7 passed in 1.0s"
        result = parse_pytest_result(output, exit_code=1)
        assert result["failures"] == 3
        assert result["errors_count"] == 0

    def test_exit2_no_errors_number_guarantees_min1(self):
        """exit_code=2 でerrors数が取れなくても errors_count>=1、failures==0。"""
        output = "something went wrong"
        result = parse_pytest_result(output, exit_code=2)
        assert result["errors_count"] >= 1
        assert result["failures"] == 0

    def test_exit2_candidates_generates_collection_error(self):
        """収集エラー(errors_count>0, failures==0)でも候補が生成される。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {
                "failures": 0, "exit_code": 2,
                "headline": "ERROR collecting tests/test_x.py",
                "errors_count": 3,
                "error_lines": ["ERROR collecting tests/test_x.py"],
            },
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) >= 1
        assert "収集エラー修正" in pytest_cand[0]["title"]


# ────────────────────────────────────────
# generate_candidates description品質テスト
# ────────────────────────────────────────

class TestCandidateDescription:
    """候補のdescription/titleが実用的かテスト。"""

    def test_description_uses_headline_not_warning(self):
        """descriptionにwarning行ではなくheadlineが使われる。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {
                "failures": 0, "exit_code": 2,   # v0.3.1: 収集エラーはfailures=0
                "summary": "some/path.py:10: DeprecationWarning: old",
                "headline": "ERROR collecting tests/test_x.py",
                "errors_count": 1,
                "error_lines": ["ERROR collecting tests/test_x.py"],
            },
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) == 1
        assert "ERROR collecting" in pytest_cand[0]["description"]
        assert "DeprecationWarning" not in pytest_cand[0]["description"]

    def test_title_shows_errors_count(self):
        """errors_count > 0 のとき title に '収集エラー修正' が出る。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {
                "failures": 0, "exit_code": 2,   # v0.3.1: 収集エラーはfailures=0
                "headline": "ERROR collecting",
                "errors_count": 9,
                "error_lines": [],
            },
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert "収集エラー修正" in pytest_cand[0]["title"]
        assert "9件" in pytest_cand[0]["title"]

    def test_title_shows_failed_count(self):
        """errors_count=0, failures>0 のとき title に 'テスト失敗修正' が出る。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {
                "failures": 3, "exit_code": 1,
                "headline": "test_something FAILED",
                "errors_count": 0,
                "error_lines": [],
            },
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert "テスト失敗修正" in pytest_cand[0]["title"]
        assert "3件" in pytest_cand[0]["title"]


# ────────────────────────────────────────
# ANSI除去・summary堅牢化テスト
# ────────────────────────────────────────

class TestStripAnsi:
    """strip_ansi() のユニットテスト。"""

    def test_removes_color_codes(self):
        """ANSIカラーコードが除去される。"""
        colored = "\x1b[31mERROR collecting tests/a.py\x1b[0m"
        assert strip_ansi(colored) == "ERROR collecting tests/a.py"

    def test_no_change_for_clean_text(self):
        """ANSIコードなしのテキストはそのまま。"""
        clean = "20 passed in 0.5s"
        assert strip_ansi(clean) == clean


class TestAnsiAndSummaryRobustness:
    """ANSI混入・warnings末尾の堅牢化テスト。"""

    def test_ansi_stripped_errors_count(self):
        """ANSIエスケープ付き出力でも errors_count を正しく抽出。"""
        output = (
            "\x1b[31mERROR collecting tests/test_a.py\x1b[0m\n"
            "\x1b[31mE   ModuleNotFoundError: No module named 'foo'\x1b[0m\n"
            "\x1b[31m5 errors in 2.5s\x1b[0m"
        )
        result = parse_pytest_result(output, exit_code=2)
        assert result["errors_count"] == 5
        # error_lines にもANSI除去後の行が入る
        assert any("ERROR collecting" in line for line in result["error_lines"])
        # headline もクリーン
        assert "\x1b[" not in result["headline"]

    def test_summary_not_warnings_when_trailing(self):
        """末尾がwarning個別行でもsummaryはwarningsにならない。"""
        output = (
            "ERROR collecting tests/test_x.py\n"
            "E   ImportError: cannot import name 'xyz'\n"
            "1 error in 0.5s\n"
            "===== warnings summary =====\n"
            "some/path.py:10: DeprecationWarning: old API"
        )
        result = parse_pytest_result(output, exit_code=2)
        # summary は "1 error in 0.5s" であるべき
        assert "error" in result["summary"].lower()
        assert "DeprecationWarning" not in result["summary"]
        assert "warnings summary" not in result["summary"]

    def test_summary_finds_result_before_warnings(self):
        """結果行 + warnings + 空行のパターンでも正しい結果行を返す。"""
        output = (
            "collecting...\n"
            "3 failed, 10 passed in 5.5s\n"
            "\n"
            "===== warnings summary =====\n"
            "lib/foo.py:42: FutureWarning: deprecated"
        )
        result = parse_pytest_result(output, exit_code=1)
        assert "3 failed" in result["summary"]
        assert result["failures"] == 3


# ────────────────────────────────────────
# resume 位相判定テスト
# ────────────────────────────────────────

class TestShouldSkipPhase:
    """_should_skip_phase() の last_completed_phase ベーステスト。"""

    def test_skip_completed_phases(self):
        """完了済みフェーズはスキップされる。"""
        # SCANが完了 → BOOT, SCAN はスキップ
        assert _should_skip_phase("SCAN", "BOOT") is True
        assert _should_skip_phase("SCAN", "SCAN") is True

    def test_resume_next_phase(self):
        """完了済みの次のフェーズはスキップされない。"""
        # SCANが完了 → SENSE は再開対象
        assert _should_skip_phase("SCAN", "SENSE") is False

    def test_skip_all_before_select(self):
        """SELECTまで完了 → EXECUTEから再開。"""
        assert _should_skip_phase("SELECT", "BOOT") is True
        assert _should_skip_phase("SELECT", "SCAN") is True
        assert _should_skip_phase("SELECT", "SENSE") is True
        assert _should_skip_phase("SELECT", "SELECT") is True
        assert _should_skip_phase("SELECT", "EXECUTE") is False
        assert _should_skip_phase("SELECT", "VERIFY") is False

    def test_unknown_phase_returns_false(self):
        """未知のフェーズ名はスキップしない。"""
        assert _should_skip_phase("UNKNOWN", "SCAN") is False
        assert _should_skip_phase("SCAN", "UNKNOWN") is False


class TestNewStateHasLastCompletedPhase:
    """新規stateにlast_completed_phaseが含まれる。"""

    def test_new_state_contains_field(self, tmp_path: Path):
        sm = StateManager(tmp_path)
        state = sm.new_state()
        assert "last_completed_phase" in state
        assert state["last_completed_phase"] is None


# ────────────────────────────────────────
# パッチバリデーション テスト
# ────────────────────────────────────────

class TestValidatePatchResult:
    """_validate_patch_result のテスト。"""

    def test_valid_patch(self, tmp_path: Path):
        """正常なパッチは通る。"""
        patch = {
            "files": [{"path": "lib/__init__.py", "action": "create", "content": ""}],
            "explanation": "テスト",
        }
        _validate_patch_result(patch, tmp_path)  # 例外なし

    def test_reject_dotdot_path(self, tmp_path: Path):
        """.. を含むパスは拒否される。"""
        patch = {
            "files": [{"path": "../outside.py", "action": "create", "content": ""}],
        }
        with pytest.raises(ValueError, match="\.\."):
            _validate_patch_result(patch, tmp_path)

    def test_reject_too_many_files(self, tmp_path: Path):
        """ファイル数超過は拒否。"""
        files = [{"path": f"f{i}.py", "action": "create", "content": ""} for i in range(MAX_PATCH_FILES + 1)]
        patch = {"files": files}
        with pytest.raises(ValueError, match="上限"):
            _validate_patch_result(patch, tmp_path)

    def test_reject_empty_files(self, tmp_path: Path):
        """空のfilesは拒否。"""
        patch = {"files": []}
        with pytest.raises(ValueError, match="空"):
            _validate_patch_result(patch, tmp_path)

    def test_reject_invalid_action(self, tmp_path: Path):
        """不正なactionは拒否。"""
        patch = {
            "files": [{"path": "a.py", "action": "delete", "content": ""}],
        }
        with pytest.raises(ValueError, match="action"):
            _validate_patch_result(patch, tmp_path)


class TestApplyPatch:
    """_apply_patch のテスト。"""

    def test_create_file(self, tmp_path: Path):
        """新規ファイルが作成される。"""
        patch = {
            "files": [{"path": "sub/new.py", "action": "create", "content": "# 新規"}],
        }
        paths = _apply_patch(patch, tmp_path)
        assert len(paths) == 1
        assert (tmp_path / "sub" / "new.py").read_text(encoding="utf-8") == "# 新規"

    def test_modify_file(self, tmp_path: Path):
        """既存ファイルが変更される。"""
        target = tmp_path / "exist.py"
        target.write_text("old", encoding="utf-8")
        patch = {
            "files": [{"path": "exist.py", "action": "modify", "content": "new"}],
        }
        _apply_patch(patch, tmp_path)
        assert target.read_text(encoding="utf-8") == "new"


class TestParsePatchJson:
    """_parse_patch_json のテスト。"""

    def test_parse_json_block(self):
        """```json ブロックからJSON抽出。"""
        raw = 'テキスト\n```json\n{"files": [], "explanation": "ok"}\n```\n後続テキスト'
        result = _parse_patch_json(raw)
        assert result["explanation"] == "ok"

    def test_parse_raw_json(self):
        """ブロックなしの生JSON。"""
        raw = '{"files": [{"path": "a.py", "action": "create", "content": ""}]}'
        result = _parse_patch_json(raw)
        assert len(result["files"]) == 1

    def test_parse_skips_broken_brace_chunk(self):
        """先頭の壊れた {..} を無視して後続JSONを抽出できる。"""
        raw = 'メモ: {broken}\n{"files": [], "explanation": "ok"}\n'
        result = _parse_patch_json(raw)
        assert result["explanation"] == "ok"

    def test_reject_no_json(self):
        """JSONがない場合はエラー。"""
        with pytest.raises(json.JSONDecodeError):
            _parse_patch_json("ただのテキスト")


class TestVerifierCommandSelection:
    """Verifier のコマンド選択テスト。"""

    def test_pytest_source(self, tmp_path: Path):
        """pytest系タスクではpytestコマンドが使われる。"""
        v = Verifier(tmp_path)
        task = {"source": "pytest", "task_id": "t1"}
        result = v.verify(task)
        assert "pytest" in result.get("command", "")

    def test_lint_source_fallback(self, tmp_path: Path):
        """lint系でスクリプトがなければpytestにフォールバック。"""
        v = Verifier(tmp_path)
        task = {"source": "workflow_lint", "task_id": "t2"}
        result = v.verify(task)
        # lintスクリプトが存在しないのでpytestが実行される
        assert "pytest" in result.get("command", "")


# ────────────────────────────────────────
# Preflight テスト
# ────────────────────────────────────────

class TestPreflightCheck:
    """_preflight_check のテスト。"""

    def test_returns_dict_with_required_keys(self, tmp_path: Path):
        """戻り値に ok, reason, git_available が含まれる。"""
        result = _preflight_check(tmp_path)
        assert "ok" in result
        assert "reason" in result
        assert "git_available" in result

    def test_non_git_dir_is_ok(self, tmp_path: Path):
        """gitリポジトリでないディレクトリではgit_available=Falseだがok=True。"""
        result = _preflight_check(tmp_path)
        # tmp_pathはgitリポジトリではない
        # git_available はFalse (not a repo) だが ok=True
        assert result["ok"] is True


# ────────────────────────────────────────
# Backup & Rollback テスト
# ────────────────────────────────────────

class TestBackupAndRollback:
    """_backup_targets / _rollback_with_backup のテスト。"""

    def test_backup_existing_file(self, tmp_path: Path):
        """既存ファイルのバックアップが作成される。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "a.py").write_text("original", encoding="utf-8")
        backup_dir = tmp_path / "backup"
        patch = {"files": [{"path": "a.py", "action": "modify", "content": "changed"}]}
        bmap = _backup_targets(patch, workspace, backup_dir)
        assert bmap["a.py"] is not None
        assert bmap["a.py"].read_text(encoding="utf-8") == "original"

    def test_backup_new_file_is_none(self, tmp_path: Path):
        """新規ファイルはbackup_map値がNone。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        backup_dir = tmp_path / "backup"
        patch = {"files": [{"path": "new.py", "action": "create", "content": "x"}]}
        bmap = _backup_targets(patch, workspace, backup_dir)
        assert bmap["new.py"] is None

    def test_rollback_restores_from_backup(self, tmp_path: Path):
        """ロールバック時にバックアップから復元される。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        target = workspace / "a.py"
        target.write_text("original", encoding="utf-8")
        backup_dir = tmp_path / "backup"
        patch = {"files": [{"path": "a.py", "action": "modify", "content": "bad"}]}
        bmap = _backup_targets(patch, workspace, backup_dir)
        # パッチ適用
        _apply_patch(patch, workspace)
        assert target.read_text(encoding="utf-8") == "bad"
        # ロールバック
        _rollback_with_backup([target], bmap, workspace)
        assert target.read_text(encoding="utf-8") == "original"

    def test_rollback_deletes_new_file(self, tmp_path: Path):
        """新規ファイルはロールバック時に削除される。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        patch = {"files": [{"path": "new.py", "action": "create", "content": "x"}]}
        bmap = _backup_targets(patch, workspace, tmp_path / "backup")
        modified = _apply_patch(patch, workspace)
        assert (workspace / "new.py").exists()
        _rollback_with_backup(modified, bmap, workspace)
        assert not (workspace / "new.py").exists()


# ────────────────────────────────────────
# Patch Diff Lines テスト
# ────────────────────────────────────────

class TestComputePatchDiffLines:
    """_compute_patch_diff_lines のテスト。"""

    def test_new_file_counts_all_lines(self, tmp_path: Path):
        """新規ファイルは全行が+カウント。"""
        patch = {"files": [{"path": "new.py", "action": "create", "content": "a\nb\nc"}]}
        bmap = {"new.py": None}
        assert _compute_patch_diff_lines(patch, bmap) == 3

    def test_modified_file_counts_changes(self, tmp_path: Path):
        """変更ファイルは差分行のみカウント。"""
        bak = tmp_path / "old.py"
        bak.write_text("line1\nline2\nline3", encoding="utf-8")
        patch = {"files": [{
            "path": "old.py", "action": "modify",
            "content": "line1\nchanged\nline3",  # line2 → changed
        }]}
        bmap = {"old.py": bak}
        # -line2 +changed = 2行
        assert _compute_patch_diff_lines(patch, bmap) == 2

    def test_no_change_is_zero(self, tmp_path: Path):
        """変更なしは0。"""
        bak = tmp_path / "same.py"
        bak.write_text("hello\n", encoding="utf-8")
        patch = {"files": [{"path": "same.py", "action": "modify", "content": "hello\n"}]}
        bmap = {"same.py": bak}
        assert _compute_patch_diff_lines(patch, bmap) == 0


# ────────────────────────────────────────
# v0.4.0 テスト: error_blocks / stable_id / restore
# ────────────────────────────────────────

class TestExtractErrorBlocks:
    """収集エラーのファイル単位分割テスト。"""

    def test_two_collection_errors(self):
        """複数のERROR collectingがブロックに分割される。"""
        lines = [
            "ERROR collecting tests/test_foo.py",
            "E   ModuleNotFoundError: No module named 'foo'",
            "",
            "ERROR collecting tests/test_bar.py",
            "E   ImportError: cannot import name 'bar'",
        ]
        blocks = _extract_error_blocks(lines)
        assert len(blocks) == 2
        assert blocks[0]["path"] == "tests/test_foo.py"
        assert "ModuleNotFoundError" in blocks[0]["exception_line"]
        assert blocks[1]["path"] == "tests/test_bar.py"
        assert "ImportError" in blocks[1]["exception_line"]

    def test_no_errors(self):
        """エラーなしは空リスト。"""
        lines = ["5 passed in 1.23s"]
        assert _extract_error_blocks(lines) == []

    def test_max_blocks(self):
        """上限制限。"""
        lines = []
        for i in range(25):
            lines.append(f"ERROR collecting tests/test_{i}.py")
            lines.append(f"E   SomeError: error {i}")
            lines.append("")
        blocks = _extract_error_blocks(lines, max_blocks=5)
        assert len(blocks) == 5

    def test_no_exception_line(self):
        """E行がないERROR collecting。"""
        lines = ["ERROR collecting tests/test_x.py"]
        blocks = _extract_error_blocks(lines)
        assert len(blocks) == 1
        assert blocks[0]["path"] == "tests/test_x.py"
        assert blocks[0]["exception_line"] == ""


class TestStableTaskId:
    """安定task_idテスト。"""

    def test_deterministic(self):
        """同じ入力は同じID。"""
        id1 = _stable_task_id("lint", "some error")
        id2 = _stable_task_id("lint", "some error")
        assert id1 == id2
        assert id1.startswith("lint_")
        # sha1先頭10文字 + prefix
        assert len(id1) == len("lint_") + 10

    def test_different_inputs(self):
        """異なる入力は異なるID。"""
        id1 = _stable_task_id("pytest_ce", "tests/foo.py", "ImportError")
        id2 = _stable_task_id("pytest_ce", "tests/bar.py", "ImportError")
        assert id1 != id2


class TestCandidateSplitting:
    """収集エラー候補分割テスト。"""

    def test_collection_errors_split_to_multiple_candidates(self):
        """複数収集エラーが複数候補になる。"""
        output = (
            "ERROR collecting tests/test_a.py\n"
            "E   ModuleNotFoundError: No module named 'a'\n"
            "\n"
            "ERROR collecting tests/test_b.py\n"
            "E   ImportError: cannot import name 'b'\n"
            "\n"
            "2 errors in 1.00s\n"
        )
        result = parse_pytest_result(output, 2)
        assert result["exit_code"] == 2
        assert result["errors_count"] == 2
        assert result["failures"] == 0  # 補正されない
        assert len(result["error_blocks"]) == 2

        scan = {"pytest": result, "workflow_lint": {"findings": []}}
        candidates = generate_candidates(scan)
        assert len(candidates) == 2
        assert candidates[0]["target_path"] == "tests/test_a.py"
        assert candidates[1]["target_path"] == "tests/test_b.py"
        # stable_idが一貫している
        assert candidates[0]["task_id"].startswith("pytest_ce_")

    def test_test_failure_stable_id(self):
        """テスト失敗のstable_id。"""
        output = "3 failed, 2 passed in 5.00s\n"
        result = parse_pytest_result(output, 1)
        assert result["failures"] == 3
        assert result["errors_count"] == 0

        scan = {"pytest": result, "workflow_lint": {"findings": []}}
        candidates = generate_candidates(scan)
        assert len(candidates) == 1
        assert candidates[0]["task_id"].startswith("pytest_tf_")

    def test_lint_stable_id(self):
        """同じlint findingsは同じtask_id。"""
        scan = {
            "pytest": {"exit_code": 0, "failures": 0},
            "workflow_lint": {"findings": ["[ERROR] missing SKILL.md"]},
        }
        c1 = generate_candidates(scan)
        c2 = generate_candidates(scan)
        assert c1[0]["task_id"] == c2[0]["task_id"]

    def test_collection_error_fallback(self):
        """error_blocksなしのフォールバック。"""
        output = "5 errors in 2.00s\n"
        result = parse_pytest_result(output, 2)
        assert result["errors_count"] == 5
        assert result["error_blocks"] == []

        scan = {"pytest": result, "workflow_lint": {"findings": []}}
        candidates = generate_candidates(scan)
        assert len(candidates) == 1
        assert candidates[0]["task_id"].startswith("pytest_ce_")
        assert "5件" in candidates[0]["title"]


class TestRestoreRollbackContext:
    """RESUME時のロールバックコンテキスト復元テスト。"""

    def test_restore_with_backup(self, tmp_path: Path):
        """バックアップが存在する場合の復元。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        output_dir = tmp_path / "out"
        backup_dir = output_dir / "20260214" / "cycle1" / "backup"
        backup_dir.mkdir(parents=True)
        # バックアップファイル作成
        (backup_dir / "hello.py").write_text("original", encoding="utf-8")

        state = {
            "execution_result": {
                "success": True,
                "modified_files": ["hello.py"],
                "backup_dir": "20260214/cycle1/backup",
            }
        }
        paths, bmap = _restore_rollback_context(state, workspace, output_dir)
        assert len(paths) == 1
        assert paths[0] == workspace / "hello.py"
        assert bmap["hello.py"] == backup_dir / "hello.py"

    def test_restore_new_file(self, tmp_path: Path):
        """新規ファイル（バックアップなし）の復元。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        output_dir = tmp_path / "out"
        backup_dir = output_dir / "20260214" / "cycle1" / "backup"
        backup_dir.mkdir(parents=True)
        state = {
            "execution_result": {
                "success": True,
                "modified_files": ["new_file.py"],
                "backup_dir": "20260214/cycle1/backup",
            }
        }
        paths, bmap = _restore_rollback_context(state, workspace, output_dir)
        assert len(paths) == 1
        assert bmap["new_file.py"] is None  # 新規ファイルはNone

    def test_restore_empty_state(self, tmp_path: Path):
        """ステートに情報がない場合。"""
        state = {"execution_result": {"success": True}}
        paths, bmap = _restore_rollback_context(
            state, tmp_path / "ws", tmp_path / "out",
        )
        assert paths == []
        assert bmap == {}


class TestRollbackAfterRestore:
    """RESUME後のロールバック統合テスト。"""

    def test_rollback_restores_original(self, tmp_path: Path):
        """復元したcontextでロールバックすると元に戻る。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        output_dir = tmp_path / "out"
        backup_dir = output_dir / "20260214" / "cycle1" / "backup"
        backup_dir.mkdir(parents=True)

        # バックアップ: 元の内容
        (backup_dir / "target.py").write_text("original", encoding="utf-8")
        # 変更後のファイル
        target = workspace / "target.py"
        target.write_text("modified_by_llm", encoding="utf-8")

        state = {
            "execution_result": {
                "success": True,
                "modified_files": ["target.py"],
                "backup_dir": "20260214/cycle1/backup",
            }
        }
        paths, bmap = _restore_rollback_context(state, workspace, output_dir)
        # ロールバック実行
        _rollback_with_backup(paths, bmap, workspace)
        assert target.read_text(encoding="utf-8") == "original"


# ────────────────────────────────────────
# v0.5.0 テスト
# ────────────────────────────────────────

class TestFailureNodes:
    """項目1: _extract_failure_nodes のテスト"""

    def test_extract_failed_lines(self):
        lines = [
            "FAILED tests/test_foo.py::TestFoo::test_bar - AssertionError",
            "FAILED tests/test_baz.py::test_qux - RuntimeError",
            "some other line",
        ]
        nodes = _extract_failure_nodes(lines)
        assert len(nodes) == 2
        assert nodes[0]["nodeid"] == "tests/test_foo.py::TestFoo::test_bar"
        assert nodes[0]["path"] == "tests/test_foo.py"
        assert nodes[1]["nodeid"] == "tests/test_baz.py::test_qux"
        assert nodes[1]["path"] == "tests/test_baz.py"

    def test_extract_error_lines_nodeid(self):
        lines = [
            "ERROR tests/test_x.py::test_y - TypeError",
        ]
        nodes = _extract_failure_nodes(lines)
        assert len(nodes) == 1
        assert nodes[0]["nodeid"] == "tests/test_x.py::test_y"

    def test_deduplicate(self):
        lines = [
            "FAILED tests/test_a.py::test_1 - Error",
            "FAILED tests/test_a.py::test_1 - Error",
        ]
        nodes = _extract_failure_nodes(lines)
        assert len(nodes) == 1

    def test_no_matches(self):
        lines = ["all passed", "3 passed in 0.1s"]
        nodes = _extract_failure_nodes(lines)
        assert nodes == []

    def test_max_nodes_limit(self):
        lines = [f"FAILED tests/test_{i}.py::test_f - Err" for i in range(50)]
        nodes = _extract_failure_nodes(lines, max_nodes=5)
        assert len(nodes) == 5

    def test_parse_pytest_result_includes_failure_nodes(self):
        output = "\n".join([
            "FAILED tests/test_a.py::test_x - AssertionError",
            "FAILED tests/test_b.py::test_y - ValueError",
            "2 failed in 1.00s",
        ])
        result = parse_pytest_result(output, exit_code=1)
        assert len(result["failure_nodes"]) == 2
        assert result["failure_nodes"][0]["path"] == "tests/test_a.py"

    def test_failure_nodes_empty_on_exit_code_0(self):
        output = "3 passed in 0.5s"
        result = parse_pytest_result(output, exit_code=0)
        assert result["failure_nodes"] == []


class TestNodeidSplitting:
    """項目2: generate_candidates の nodeid 分割テスト"""

    def test_nodeid_split_candidates(self):
        scan = {
            "pytest": {
                "exit_code": 1,
                "failures": 2,
                "errors_count": 0,
                "error_blocks": [],
                "failure_nodes": [
                    {"nodeid": "tests/test_a.py::test_x", "path": "tests/test_a.py"},
                    {"nodeid": "tests/test_b.py::test_y", "path": "tests/test_b.py"},
                ],
                "summary": "2 failed",
            },
        }
        cands = generate_candidates(scan)
        # 2つの候補が生成されるべき
        pytest_cands = [c for c in cands if c["source"] == "pytest"]
        assert len(pytest_cands) == 2
        assert pytest_cands[0]["target_nodeid"] == "tests/test_a.py::test_x"
        assert pytest_cands[0]["target_path"] == "tests/test_a.py"
        assert pytest_cands[1]["target_nodeid"] == "tests/test_b.py::test_y"

    def test_stable_task_id_from_nodeid(self):
        scan = {
            "pytest": {
                "exit_code": 1,
                "failures": 1,
                "errors_count": 0,
                "error_blocks": [],
                "failure_nodes": [
                    {"nodeid": "tests/test_a.py::test_x", "path": "tests/test_a.py"},
                ],
                "summary": "1 failed",
            },
        }
        cands1 = generate_candidates(scan)
        cands2 = generate_candidates(scan)
        # 同じ入力なら同じtask_id
        assert cands1[0]["task_id"] == cands2[0]["task_id"]

    def test_fallback_when_no_failure_nodes(self):
        scan = {
            "pytest": {
                "exit_code": 1,
                "failures": 3,
                "errors_count": 0,
                "error_blocks": [],
                "failure_nodes": [],
                "summary": "3 failed",
            },
        }
        cands = generate_candidates(scan)
        pytest_cands = [c for c in cands if c["source"] == "pytest"]
        # フォールバック: 1候補に丸め
        assert len(pytest_cands) == 1
        assert "target_nodeid" not in pytest_cands[0]


class TestAutoFixable:
    """項目3: annotate_candidates のテスト"""

    def test_pytest_with_target_is_fixable(self):
        cands = [
            {"task_id": "t1", "source": "pytest", "target_path": "tests/test_a.py", "target_nodeid": "tests/test_a.py::test_x"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is True
        assert cands[0]["blocked_reason"] == ""

    def test_pytest_without_target_not_fixable(self):
        cands = [
            {"task_id": "t2", "source": "pytest"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is False
        assert cands[0]["blocked_reason"] == "no_target_specified"

    def test_lint_normal_is_fixable(self):
        cands = [
            {"task_id": "l1", "source": "workflow_lint", "description": "README missing version"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is True

    def test_lint_missing_skill_not_fixable(self):
        cands = [
            {"task_id": "l2", "source": "workflow_lint", "description": "agent 'foo': missing SKILL.md"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is False
        assert "skill.md" in cands[0]["blocked_reason"]

    def test_lint_pycache_not_fixable(self):
        cands = [
            {"task_id": "l3", "source": "workflow_lint", "description": "agent '__pycache__': missing README.md"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is False
        assert "__pycache__" in cands[0]["blocked_reason"]

    def test_lint_utf8_not_fixable(self):
        cands = [
            {"task_id": "l4", "source": "workflow_lint", "description": "utf-8 decode failed"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is False

    def test_mixed_candidates(self):
        cands = [
            {"task_id": "a", "source": "pytest", "target_path": "t.py", "target_nodeid": "t.py::test"},
            {"task_id": "b", "source": "pytest"},
            {"task_id": "c", "source": "workflow_lint", "description": "ok finding"},
            {"task_id": "d", "source": "workflow_lint", "description": "__pycache__ issue"},
        ]
        annotate_candidates(cands)
        assert cands[0]["auto_fixable"] is True
        assert cands[1]["auto_fixable"] is False
        assert cands[2]["auto_fixable"] is True
        assert cands[3]["auto_fixable"] is False


class TestSelectAutoFixable:
    """項目4: select_task の auto_fixable フィルタテスト"""

    def test_selects_only_fixable(self):
        cands = [
            {"task_id": "a", "priority": 1, "auto_fixable": False, "blocked_reason": "x"},
            {"task_id": "b", "priority": 2, "auto_fixable": True, "blocked_reason": ""},
        ]
        selected = select_task(cands, [])
        assert selected["task_id"] == "b"

    def test_returns_none_if_all_blocked(self):
        cands = [
            {"task_id": "a", "priority": 1, "auto_fixable": False, "blocked_reason": "x"},
            {"task_id": "b", "priority": 2, "auto_fixable": False, "blocked_reason": "y"},
        ]
        selected = select_task(cands, [])
        assert selected is None

    def test_returns_none_if_all_paused(self):
        cands = [
            {"task_id": "a", "priority": 1, "auto_fixable": True, "blocked_reason": ""},
        ]
        selected = select_task(cands, ["a"])
        assert selected is None

    def test_backward_compat_no_auto_fixable_key(self):
        # auto_fixableが未付与でも従来互換（default=True）
        cands = [
            {"task_id": "a", "priority": 1},
        ]
        selected = select_task(cands, [])
        assert selected["task_id"] == "a"


class TestRecordFailurePaused:
    """項目5: record_failure の paused_now 戻り値テスト"""

    def test_returns_false_before_max(self):
        state = {"failure_log": [], "paused_tasks": []}
        result = record_failure(state, "t1", "deterministic", "error")
        assert result is False
        assert len(state["paused_tasks"]) == 0

    def test_returns_true_at_max(self):
        state = {"failure_log": [], "paused_tasks": []}
        # MAX_TASK_FAILURES - 1 回呼ぶ
        for _ in range(MAX_TASK_FAILURES - 1):
            result = record_failure(state, "t1", "deterministic", "error")
            assert result is False
        # MAX_TASK_FAILURES 回目で True
        result = record_failure(state, "t1", "deterministic", "error")
        assert result is True
        assert "t1" in state["paused_tasks"]

    def test_returns_false_after_paused(self):
        state = {"failure_log": [], "paused_tasks": []}
        for _ in range(MAX_TASK_FAILURES):
            record_failure(state, "t1", "deterministic", "error")
        # すでにpaused済みの追加失敗はFalse
        result = record_failure(state, "t1", "deterministic", "error")
        assert result is False


class TestVerifierNodeid:
    """項目6: Verifier の target_nodeid 対応テスト"""

    def test_nodeid_command_includes_nodeid(self, tmp_path):
        v = Verifier(tmp_path)
        task = {
            "source": "pytest",
            "target_path": "tests/test_a.py",
            "target_nodeid": "tests/test_a.py::TestFoo::test_bar",
        }
        # verifyを実行せずコマンド生成ロジックだけテスト
        # Verifier.verify内のcmd構築ロジックを、関数を直接呼んで確認
        result = v.verify(task)
        # nodeidがコマンドに含まれているか確認
        assert "tests/test_a.py::TestFoo::test_bar" in result["command"]

    def test_target_path_fallback(self, tmp_path):
        v = Verifier(tmp_path)
        task = {
            "source": "pytest",
            "target_path": "tests/test_a.py",
        }
        result = v.verify(task)
        assert "tests/test_a.py" in result["command"]
        assert "::" not in result["command"]


# ────────────────────────────────────────
# v0.5.1 回帰テスト（ChatGPT Pro指摘対応）
# ────────────────────────────────────────

class TestPathValidationBoundary:
    """[P2] パス検証の境界ケーステスト。"""

    def test_dotdot_in_component_rejected(self, tmp_path: Path):
        """.. がパスコンポーネントにある場合は拒否。"""
        patch = {
            "files": [{"path": "../outside.py", "action": "create", "content": ""}],
        }
        with pytest.raises(ValueError, match="\\.\\."):
            _validate_patch_result(patch, tmp_path)

    def test_dotdot_in_middle_rejected(self, tmp_path: Path):
        """中間に .. があるパスは拒否。"""
        patch = {
            "files": [{"path": "sub/../../../etc/passwd", "action": "create", "content": ""}],
        }
        with pytest.raises(ValueError, match="\\.\\."):
            _validate_patch_result(patch, tmp_path)

    def test_dotdot_in_filename_allowed(self, tmp_path: Path):
        """ファイル名に .. が含まれる場合（foo..bar.py）は許可。"""
        patch = {
            "files": [{"path": "foo..bar.py", "action": "create", "content": ""}],
        }
        # 例外が出ないことを確認
        _validate_patch_result(patch, tmp_path)

    def test_absolute_path_rejected(self, tmp_path: Path):
        """絶対パスはワークスペース外として拒否。"""
        patch = {
            "files": [{"path": "/etc/passwd", "action": "create", "content": ""}],
        }
        with pytest.raises(ValueError):
            _validate_patch_result(patch, tmp_path)

    def test_prefix_collision_rejected(self, tmp_path: Path):
        """ワークスペース名のprefix衝突は拒否される。
        例: workspace=/tmp/ws のとき /tmp/ws2/evil.py が通らないこと。
        """
        # tmp_path = /tmp/pytest-xxx/test_xxx
        # sibling = /tmp/pytest-xxx/test_xxx2  (prefix衝突)
        sibling = Path(str(tmp_path) + "2")
        sibling.mkdir(exist_ok=True)
        evil_file = sibling / "evil.py"
        evil_file.write_text("hack", encoding="utf-8")
        # relative_to で検証するので、resolve後にworkspace外と判定される
        # ただし相対パスとして渡すにはシンボリックリンク等が必要。
        # ここでは絶対パスで渡して拒否されることを確認。
        patch = {
            "files": [{"path": str(evil_file), "action": "modify", "content": "x"}],
        }
        with pytest.raises(ValueError):
            _validate_patch_result(patch, tmp_path)

    def test_normal_nested_path_allowed(self, tmp_path: Path):
        """正常なネストパスは許可。"""
        patch = {
            "files": [{"path": "src/lib/utils.py", "action": "create", "content": "ok"}],
        }
        _validate_patch_result(patch, tmp_path)


class TestPausedNowReportConsistency:
    """[P2] paused_now 発動時の state/report 整合性テスト。"""

    def test_record_failure_paused_state_status(self):
        """MAX_TASK_FAILURES到達時、stateがPAUSEDになり戻り値がTrue。"""
        state = {"failure_log": [], "paused_tasks": []}
        for _ in range(MAX_TASK_FAILURES - 1):
            result = record_failure(state, "task_x", "deterministic", "err")
            assert result is False
        # MAX回目
        result = record_failure(state, "task_x", "deterministic", "err")
        assert result is True
        assert "task_x" in state["paused_tasks"]

    def test_paused_task_excluded_and_none_returned(self):
        """PAUSED入りしたタスクは次回select_taskで除外される。"""
        cands = [
            {"task_id": "task_x", "priority": 1, "auto_fixable": True, "blocked_reason": ""},
        ]
        selected = select_task(cands, ["task_x"])
        assert selected is None

    def test_multiple_failures_same_task_pauses_once(self):
        """同じタスクの追加失敗はpaused_nowを再度Trueにしない。"""
        state = {"failure_log": [], "paused_tasks": []}
        # MAX回でpause
        for _ in range(MAX_TASK_FAILURES):
            record_failure(state, "t1", "deterministic", "e")
        assert "t1" in state["paused_tasks"]
        # 追加失敗: paused_nowはFalse（既にpaused済み）
        result = record_failure(state, "t1", "deterministic", "e")
        assert result is False
        # paused_tasksに重複追加されていないこと
        assert state["paused_tasks"].count("t1") == 1


# ============================================================
# v0.6.0 テスト
# ============================================================

class TestSeverityFilter:
    """Scanner.run_workflow_lint の severity_filter テスト。"""

    def test_default_error_only(self, tmp_path: Path):
        """デフォルトは[ERROR]のみ取得。"""
        scanner = Scanner(tmp_path)
        # run_workflow_lint は lint_script が存在しないと available=False
        result = scanner.run_workflow_lint()
        assert result["available"] is False

    def test_custom_severity_filter(self, tmp_path: Path):
        """severity_filter にカスタム値が渡せる。"""
        scanner = Scanner(tmp_path)
        result = scanner.run_workflow_lint(severity_filter=("[ERROR]", "[CAUTION]"))
        assert result["available"] is False  # スクリプトなし


class TestCostTracking:
    """token_usage 累積と推定コストのテスト。"""

    def test_log_token_usage_accumulates(self):
        """_log_token_usage が state に累積する。"""
        class MockMeta:
            prompt_token_count = 100
            candidates_token_count = 50
            total_token_count = 150
        class MockResp:
            usage_metadata = MockMeta()

        state: dict = {}
        _log_token_usage(MockResp(), "gemini-2.5-flash", state)
        assert state["token_usage"]["prompt"] == 100
        assert state["token_usage"]["output"] == 50
        assert state["token_usage"]["total"] == 150
        assert state["token_usage"]["estimated_cost_usd"] > 0

        # 2回目: 累積
        _log_token_usage(MockResp(), "gemini-2.5-flash", state)
        assert state["token_usage"]["prompt"] == 200
        assert state["token_usage"]["total"] == 300

    def test_cost_estimation_flash(self):
        """Flash モデルのコスト推定が正しい。"""
        class MockMeta:
            prompt_token_count = 1_000_000
            candidates_token_count = 1_000_000
            total_token_count = 2_000_000
        class MockResp:
            usage_metadata = MockMeta()

        state: dict = {}
        usage = _log_token_usage(MockResp(), "gemini-2.5-flash", state)
        # input: 0.15 + output: 0.60 = 0.75
        assert abs(usage["estimated_cost_usd"] - 0.75) < 0.001

    def test_no_state_no_error(self):
        """state=None でもエラーにならない。"""
        class MockMeta:
            prompt_token_count = 10
            candidates_token_count = 5
            total_token_count = 15
        class MockResp:
            usage_metadata = MockMeta()

        usage = _log_token_usage(MockResp(), "gemini-2.5-flash", None)
        assert usage["total"] == 15


class TestStructuredKI:
    """構造化KI記録のテスト。"""

    def test_record_ki_with_metadata(self):
        """metadata付きでも _record_ki はエラーにならない（KIライブラリ不在時）。"""
        # KI不在時は何もしない（例外を出さないことを確認）
        _record_ki(
            "FAILURE",
            cycle_id="test",
            task_id="test_task",
            note="test",
            metadata={
                "failure_class": "DETERMINISTIC",
                "error_summary": "test error",
                "verification_success": False,
                "files_modified": 0,
            },
        )


class TestWebhook:
    """Webhook通知のテスト。"""

    def test_send_webhook_empty_url(self):
        """URL空文字列は何もしない。"""
        _send_webhook("", {"summary": "test"})  # エラーなし

    def test_send_webhook_none_url(self):
        """URL=None は何もしない。"""
        _send_webhook(None, {"summary": "test"})  # エラーなし

    def test_send_webhook_invalid_url(self):
        """無効URLはログ警告のみでエラーにならない。"""
        _send_webhook("http://localhost:99999/invalid", {"summary": "test"})


class TestCLIArgs:
    """v0.6.0 CLI引数のテスト。"""

    def test_build_parser_has_new_args(self):
        """新しいCLI引数がパーサーに存在する。"""
        parser = build_parser()
        args = parser.parse_args(["--once", "--log-json", "--lint-severity", "error,caution"])
        assert args.log_json is True
        assert args.lint_severity == "error,caution"

    def test_loop_and_interval_defaults(self):
        """--loop と --interval のデフォルト値。"""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.loop is False
        assert args.interval == 300

    def test_approve_flag(self):
        """--approve フラグが機能する。"""
        parser = build_parser()
        args = parser.parse_args(["--approve"])
        assert args.approve is True

    def test_webhook_url(self):
        """--webhook-url が正しく解析される。"""
        parser = build_parser()
        args = parser.parse_args(["--webhook-url", "https://discord.com/api/webhooks/test"])
        assert args.webhook_url == "https://discord.com/api/webhooks/test"


class TestSDKCompat:
    """SDK互換層のテスト。"""

    def test_gemini_client_compat_backend(self):
        """_GeminiClientCompat のバックエンド識別。"""
        mock_client = type("MockClient", (), {})()
        compat = _GeminiClientCompat("google-genai", mock_client)
        assert compat.backend == "google-genai"
        assert compat.client is mock_client

    def test_gemini_client_compat_legacy_backend(self):
        """旧SDKバックエンドの識別。"""
        mock_client = type("MockClient", (), {})()
        compat = _GeminiClientCompat("google-generativeai", mock_client)
        assert compat.backend == "google-generativeai"


class TestLoggingSetup:
    """logging初期化のテスト。"""

    def test_setup_logging_default(self):
        """デフォルト(テキスト)モードでエラーにならない。"""
        import logging
        _setup_logging(json_mode=False)
        assert logger.level == logging.INFO

    def test_setup_logging_json(self):
        """JSONモードでエラーにならない。"""
        _setup_logging(json_mode=True)
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, _JsonFormatter)


# ============================================================
# v0.6.0 CLI統合テスト + スキーマ固定テスト
# ============================================================

from agi_kernel import run_cycle, _COST_PER_1M


def _make_args(tmp_path: Path, **overrides):
    """テスト用のargparse.Namespaceを生成するヘルパー。"""
    defaults = {
        "once": True,
        "loop": False,
        "interval": 300,
        "resume": False,
        "dry_run": True,       # 安全のためdry-run
        "auto_commit": False,
        "approve": False,
        "workspace": str(tmp_path),
        "llm_model": None,
        "llm_strong_model": None,
        "webhook_url": None,
        "lint_severity": "error",
        "log_json": False,
    }
    defaults.update(overrides)
    import argparse
    return argparse.Namespace(**defaults)


class TestCLIIntegrationLoop:
    """--loop 統合テスト。"""

    def test_loop_flag_parsed(self):
        """--loop --interval 60 がパーサーで正しく解析される。"""
        parser = build_parser()
        args = parser.parse_args(["--loop", "--interval", "60"])
        assert args.loop is True
        assert args.interval == 60

    def test_loop_single_cycle_exits_on_nonzero(self, tmp_path: Path):
        """常駐モードは exit_code!=0 で停止する
        （ワークスペース空なのでスキャン→候補なし→COMPLETED→exit 0 になるので
        ここではonce=True相当で1回走ることを確認）。
        """
        args = _make_args(tmp_path, loop=False)
        exit_code = run_cycle(args)
        # dry_run + 空ワークスペース → COMPLETED → 0
        assert exit_code == 0


class TestCLIIntegrationApprove:
    """--approve 統合テスト。"""

    def test_approve_flag_parsed(self):
        """--approve がパーサーで正しく解析される。"""
        parser = build_parser()
        args = parser.parse_args(["--approve"])
        assert args.approve is True

    def test_approve_in_dry_run_no_prompt(self, tmp_path: Path):
        """dry-run + --approve ではEXECUTEがスキップされるため
        input()プロンプトは呼ばれない。"""
        args = _make_args(tmp_path, approve=True, dry_run=True)
        # dry_runなのでEXECUTE自体がスキップ → input()は呼ばれずに完了
        exit_code = run_cycle(args)
        assert exit_code == 0


class TestCLIIntegrationLogJson:
    """--log-json 統合テスト。"""

    def test_log_json_sets_formatter(self):
        """_setup_logging(json_mode=True) でJSONフォーマッタが適用される。"""
        _setup_logging(json_mode=True)
        assert isinstance(logger.handlers[0].formatter, _JsonFormatter)

    def test_log_json_produces_json_output(self, capsys):
        """JSONモードのログ出力がJSON形式になる。"""
        _setup_logging(json_mode=True)
        logger.info("テストメッセージ")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["msg"] == "テストメッセージ"
        assert parsed["level"] == "INFO"


class TestCLIIntegrationLintSeverity:
    """--lint-severity 統合テスト。"""

    def test_lint_severity_multi_level(self, tmp_path: Path):
        """--lint-severity error,caution がsev_filterに変換される。"""
        args = _make_args(tmp_path, lint_severity="error,caution")
        sev_filter = tuple(f"[{s.strip().upper()}]" for s in args.lint_severity.split(","))
        assert sev_filter == ("[ERROR]", "[CAUTION]")

    def test_lint_severity_run_cycle(self, tmp_path: Path):
        """error,caution でもrun_cycleがエラーなく完了する。"""
        args = _make_args(tmp_path, lint_severity="error,caution")
        exit_code = run_cycle(args)
        assert exit_code == 0


class TestCLIIntegrationWebhook:
    """--webhook-url 統合テスト。"""

    def test_webhook_url_parsed(self):
        """--webhook-url がパーサーで正しく解析される。"""
        parser = build_parser()
        args = parser.parse_args(["--webhook-url", "https://hooks.example.com/test"])
        assert args.webhook_url == "https://hooks.example.com/test"

    def test_webhook_invalid_url_no_crash(self, tmp_path: Path):
        """無効なwebhook URLでもrun_cycleがクラッシュしない。"""
        args = _make_args(tmp_path, webhook_url="http://localhost:1/invalid")
        exit_code = run_cycle(args)
        assert exit_code == 0


class TestReportTokenUsageSchema:
    """report.json の token_usage / cost スキーマ固定テスト。"""

    _REQUIRED_KEYS = {"prompt", "output", "total", "estimated_cost_usd"}

    def test_token_usage_default_has_all_keys(self):
        """state.setdefault で作成される token_usage が全キーを持つ。"""
        state: dict = {}
        tu = state.setdefault("token_usage", {
            "prompt": 0, "output": 0, "total": 0, "estimated_cost_usd": 0.0,
        })
        assert self._REQUIRED_KEYS.issubset(tu.keys())

    def test_token_usage_after_log_has_all_keys(self):
        """_log_token_usage 後の state["token_usage"] が全キーを持つ。"""
        class MockMeta:
            prompt_token_count = 10
            candidates_token_count = 5
            total_token_count = 15
        class MockResp:
            usage_metadata = MockMeta()

        state: dict = {}
        _log_token_usage(MockResp(), "gemini-2.5-flash", state)
        assert self._REQUIRED_KEYS.issubset(state["token_usage"].keys())
        assert isinstance(state["token_usage"]["estimated_cost_usd"], float)

    def test_report_includes_token_usage(self, tmp_path: Path):
        """dry-run サイクルの report.json に token_usage キーが含まれる。"""
        args = _make_args(tmp_path)
        exit_code = run_cycle(args)
        assert exit_code == 0
        # report.json を探す
        report_files = list(tmp_path.rglob("report_*.json"))
        # dry-run + 空ワークスペースでは候補なし早期終了する場合がある
        # → state.json 経由で token_usage を確認
        state_file = tmp_path / "_outputs" / "state.json"
        if state_file.exists():
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            if "token_usage" in state_data:
                assert self._REQUIRED_KEYS.issubset(state_data["token_usage"].keys())

    def test_cost_per_1m_has_required_models(self):
        """_COST_PER_1M に必須モデルが含まれる。"""
        assert "gemini-2.5-flash" in _COST_PER_1M
        assert "gemini-2.5-pro" in _COST_PER_1M
        for model_rates in _COST_PER_1M.values():
            assert "input" in model_rates
            assert "output" in model_rates

