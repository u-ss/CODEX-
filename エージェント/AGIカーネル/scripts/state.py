#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — 状態管理モジュール (v0.6.1)

FileLock, StateManager, classify_failure, record_failure。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agi_kernel")

# タイムゾーン: JST
JST = timezone(timedelta(hours=9))

# ── 定数 ──
FAILURE_CATEGORIES = frozenset({
    "TRANSIENT", "DETERMINISTIC", "ENVIRONMENT", "FLAKY", "POLICY"
})
MAX_TASK_FAILURES = 3
LOCK_TTL_SECONDS = 600  # 10分


# ============================================================
# 失敗分類
# ============================================================

def classify_failure(error_msg: str) -> str:
    """エラーメッセージから失敗カテゴリを推定する。"""
    msg = error_msg.lower()
    if any(k in msg for k in ("timeout", "timed out", "connection reset")):
        return "TRANSIENT"
    if any(k in msg for k in ("modulenotfounderror", "importerror", "no module named")):
        return "ENVIRONMENT"
    if any(k in msg for k in ("permission denied", "access denied")):
        return "POLICY"
    if any(k in msg for k in ("flaky", "intermittent")):
        return "FLAKY"
    return "DETERMINISTIC"


# ============================================================
# FileLock — 多重起動防止
# ============================================================

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

        # stale lock 回収
        if self.lock_path.exists():
            try:
                content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                ts = content.get("timestamp") or content.get("created_at", 0)
                if time.time() - ts > self.ttl:
                    logger.info(f"[LOCK] Stale lock 回収 (age={time.time()-ts:.0f}s)")
                    self.lock_path.unlink(missing_ok=True)
                else:
                    return False
            except (json.JSONDecodeError, OSError):
                self.lock_path.unlink(missing_ok=True)

        # 排他的作成
        try:
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            info = json.dumps({
                "pid": os.getpid(),
                "timestamp": time.time(),
            })
            os.write(fd, info.encode("utf-8"))
            os.close(fd)
            self._acquired = True
            return True
        except FileExistsError:
            return False
        except OSError:
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
        self.bak_path = output_dir / "state.json.bak"

    # 後方互換プロパティ (テスト向け)
    @property
    def _bak_path(self) -> Path:
        return self.bak_path

    @property
    def _tmp_path(self) -> Path:
        return self.state_path.with_suffix(".json.tmp")

    def new_state(self) -> dict[str, Any]:
        """新規サイクル用の初期stateを生成する。"""
        now = datetime.now(tz=JST)
        cycle_id = now.strftime("%H%M%S")
        return {
            "version": "0.6.1",
            "cycle_id": cycle_id,
            "date_str": now.strftime("%Y-%m-%d"),
            "started_at": now.isoformat(),
            "phase": "BOOT",
            "status": "RUNNING",
            "last_completed_phase": None,
            "scan_results": {},
            "candidates": [],
            "blocked_candidates": [],
            "selected_task": None,
            "execution_result": {},
            "verification_result": {},
            "outcome": "",
            "failure_log": [],
            "paused_tasks": [],
            "ki_records": [],
            "token_usage": {"prompt": 0, "output": 0, "total": 0, "estimated_cost_usd": 0.0},
        }

    def load(self) -> Optional[dict[str, Any]]:
        """state.jsonを読み込む。壊れていれば.bakをフォールバック。"""
        state = self._try_load(self.state_path)
        if state is not None:
            return state
        # fallback
        state = self._try_load(self.bak_path)
        if state:
            logger.warning("[STATE] state.json が壊れているため .bak から復旧")
        return state

    @staticmethod
    def _try_load(path: Path) -> Optional[dict[str, Any]]:
        """指定パスのJSONを読み込む。失敗ならNone。"""
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            return json.loads(text)
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
        # 1. backup
        if self.state_path.exists():
            try:
                shutil.copy2(str(self.state_path), str(self.bak_path))
            except OSError:
                pass
        # 2. tmp write
        tmp = self.state_path.with_suffix(".json.tmp")
        data = json.dumps(state, ensure_ascii=False, indent=2, default=str)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # 3. atomic replace
        os.replace(str(tmp), str(self.state_path))

    def save_candidates(
        self, candidates: list[dict], date_str: str, cycle_id: str,
    ) -> Path:
        """cycle_idフォルダにcandidates.jsonを保存し、latestにもコピーする。

        保存先: {output_dir}/{date_str}/{cycle_id}/candidates.json
        latest: {output_dir}/{date_str}/latest_candidates.json

        Returns:
            保存先のPathオブジェクト
        """
        cycle_dir = self.output_dir / date_str / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        dest = cycle_dir / "candidates.json"
        data = json.dumps(candidates, ensure_ascii=False, indent=2, default=str)
        dest.write_text(data, encoding="utf-8")
        # latest
        latest = self.output_dir / date_str / "latest_candidates.json"
        try:
            shutil.copy2(str(dest), str(latest))
        except OSError:
            pass
        return dest

    def save_report(
        self, report: dict, date_str: str, cycle_id: str,
    ) -> Path:
        """cycle_idフォルダにreport.jsonを保存し、latestにもコピーする。

        保存先: {output_dir}/{date_str}/{cycle_id}/report.json
        latest: {output_dir}/{date_str}/latest_report.json

        Returns:
            保存先のPathオブジェクト
        """
        cycle_dir = self.output_dir / date_str / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        dest = cycle_dir / "report.json"
        data = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        dest.write_text(data, encoding="utf-8")
        # latest
        latest = self.output_dir / date_str / "latest_report.json"
        try:
            shutil.copy2(str(dest), str(latest))
        except OSError:
            pass
        return dest


# ============================================================
# 失敗ログ管理
# ============================================================

def record_failure(state: dict, task_id: str, category: str, error: str) -> bool:
    """失敗をstateに記録し、3回でPAUSEDにする。

    Returns:
        bool: True = この呼び出しで paused_tasks に追加された（即停止すべき）
    """
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
        return True
    return False


# ============================================================
# KI Learning 記録
# ============================================================

# KI Learning フック（任意）
_HAS_KI = False
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(".agent/workflows/shared")))
    from ki_learning_hook import report_action_outcome  # type: ignore
    _HAS_KI = True
except ImportError:
    pass


def record_ki(outcome: str, *, metadata: Optional[dict] = None, **kwargs) -> None:
    """構造化KI Learning記録。

    Args:
        outcome: SUCCESS / FAILURE / PARTIAL
        metadata: 構造化データ（failure_class, diff_summary 等）
        **kwargs: report_action_outcomeに渡す追加引数
    """
    if not _HAS_KI:
        return
    try:
        extra: dict[str, Any] = {}
        if metadata:
            extra["metadata"] = json.dumps(metadata, ensure_ascii=False, default=str)
        report_action_outcome(
            agent="/agi_kernel",
            intent_class="agi_kernel_cycle",
            outcome=outcome,
            **extra,
            **kwargs,
        )
    except Exception:
        pass  # ライブラリ不在/エラーでも落ちない
