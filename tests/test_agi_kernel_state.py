#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — state.json の save/load/resume ユニットテスト
"""

import json
import sys
from pathlib import Path

import pytest

# テスト対象モジュールをインポートできるようにパスを追加
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_DIR = _REPO_ROOT / "エージェント" / "AGIカーネル" / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from agi_kernel import (
    StateManager,
    classify_failure,
    generate_candidates,
    select_task,
    record_failure,
    MAX_TASK_FAILURES,
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
        """candidates.jsonの保存テスト。"""
        sm = StateManager(tmp_path)
        candidates = [{"task_id": "lint_000", "priority": 1}]
        path = sm.save_candidates(candidates, "20260214")
        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["task_id"] == "lint_000"

    def test_save_report(self, tmp_path: Path):
        """report.jsonの保存テスト。"""
        sm = StateManager(tmp_path)
        report = {"cycle_id": "test", "status": "COMPLETED"}
        path = sm.save_report(report, "20260214")
        assert path.exists()


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
        # 完了済み → 新規サイクル開始（ロジックはrun_cycle内）

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
