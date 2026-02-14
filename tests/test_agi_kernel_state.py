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
    Verifier,
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
        assert len(pytest_cand) == 1
        assert pytest_cand[0]["task_id"] == "pytest_exit_2"

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
        """exit_code=1, failures=3 → 安定IDが pytest_exit_1。"""
        scan = {
            "workflow_lint": {"errors": 0, "findings": []},
            "pytest": {"failures": 3, "exit_code": 1, "summary": "3 failed, 10 passed"},
        }
        candidates = generate_candidates(scan)
        pytest_cand = [c for c in candidates if c["source"] == "pytest"]
        assert len(pytest_cand) == 1
        assert pytest_cand[0]["task_id"] == "pytest_exit_1"


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

