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
import difflib
import hashlib
import re
from abc import ABC, abstractmethod
import textwrap

__version__ = "0.5.2"

import argparse
import json
import os
import random

# .env 自動読み込み（python-dotenvがあれば）
try:
    from dotenv import load_dotenv
    # スクリプトからワークスペースルートの .env を探す
    _dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
    if os.path.isfile(_dotenv_path):
        load_dotenv(_dotenv_path, override=False)
except ImportError:
    pass  # dotenv 未インストールでも動作可能

import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# ── ロガー初期化 ──
logger = logging.getLogger("agi_kernel")


class _JsonFormatter(logging.Formatter):
    """JSON構造化ログフォーマッタ。"""
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "phase": getattr(record, "phase", ""),
            "msg": record.getMessage(),
        }
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging(*, json_mode: bool = False, level: int = logging.INFO) -> None:
    """ロギング初期設定。json_mode=True でJSON構造化出力。"""
    handler = logging.StreamHandler(sys.stdout)
    if json_mode:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

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

# ── EXECUTE/VERIFY 安全制限 ──
MAX_PATCH_FILES = 5          # 1回のパッチで変更可能な最大ファイル数
MAX_DIFF_LINES = 200         # 1回のパッチの最大diff行数
MAX_LLM_RETRIES = 3          # LLM出力バリデーション失敗時の最大リトライ
LLM_RETRY_BASE_DELAY_SECONDS = 1.0  # LLM再試行の初期待機秒
LLM_RETRY_MAX_DELAY_SECONDS = 8.0   # LLM再試行の最大待機秒
LLM_RETRY_JITTER_SECONDS = 0.3      # 同時再試行回避のジッター

# ── コスト推定定数（USD / 1M tokens, 2026-02時点） ──
_COST_PER_1M: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
}

COMMAND_ALLOWLIST = [         # VERIFY で実行を許可するコマンド
    [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"],
    [sys.executable, "tools/workflow_lint.py"],
]


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
            "last_completed_phase": None,
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

    def save_candidates(self, candidates: list[dict], date_str: str, cycle_id: str) -> Path:
        """cycle_idフォルダにcandidates.jsonを保存し、latestにもコピーする。

        保存先: {output_dir}/{date_str}/{cycle_id}/candidates.json
        latest: {output_dir}/{date_str}/latest_candidates.json
        """
        cycle_dir = self.output_dir / date_str / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        path = cycle_dir / "candidates.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        # latest コピー
        latest = self.output_dir / date_str / "latest_candidates.json"
        try:
            shutil.copy2(str(path), str(latest))
        except OSError:
            pass  # latest コピー失敗は致命的でない
        return path

    def save_report(self, report: dict, date_str: str, cycle_id: str) -> Path:
        """cycle_idフォルダにreport.jsonを保存し、latestにもコピーする。

        保存先: {output_dir}/{date_str}/{cycle_id}/report.json
        latest: {output_dir}/{date_str}/latest_report.json
        """
        cycle_dir = self.output_dir / date_str / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        path = cycle_dir / "report.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        # latest コピー
        latest = self.output_dir / date_str / "latest_report.json"
        try:
            shutil.copy2(str(path), str(latest))
        except OSError:
            pass  # latest コピー失敗は致命的でない
        return path


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


# ── テスト失敗ノード抽出（nodeid単位分割用） ──
_RE_FAILED_LINE = re.compile(
    r"^FAILED\s+(.+?)\s*(?:-\s*.+)?$"
)
_RE_ERROR_LINE = re.compile(
    r"^ERROR\s+(.+?)\s*(?:-\s*.+)?$"
)


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
            # path = nodeid の :: 前
            path = nodeid.split("::")[0]
            nodes.append({"nodeid": nodeid, "path": path})
            if len(nodes) >= max_nodes:
                break
    return nodes



# ── エラーブロック抽出（ファイル単位の収集エラー分割用） ──
_RE_ERROR_COLLECTING = re.compile(
    r"ERROR\s+collecting\s+(.+?)(?:\s+|$)"
)


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
            # 直後のE行を収集
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
                "snippet": snippet[:8],  # スニペット最大8行
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
    # 末尾から逆走査し、結果キーワードを含む行を探す
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # warnings summary ヘッダやwarning個別行はスキップ
        if "warnings summary" in stripped.lower() or "Warning:" in stripped:
            continue
        # 結果行キーワードを含むか
        lower = stripped.lower()
        for kw in _SUMMARY_KEYWORDS:
            if kw in lower:
                return stripped
    # フォールバック: 末尾行
    return lines[-1].strip() if lines else ""


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
    # ANSI除去（--color=no の保険）
    clean_output = strip_ansi(output) if output else ""
    lines = clean_output.strip().splitlines() if clean_output.strip() else []
    tail = lines[-20:] if lines else []  # デバッグ用に末尾20行

    # summary: 末尾から結果行を探索（warningsを飛ばす）
    summary = _find_summary_line(lines) if lines else ""

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
    m = _RE_ERRORS_IN.search(clean_output)
    if m:
        errors_count = int(m.group(1))
    else:
        m = _RE_INTERRUPTED.search(clean_output)
        if m:
            errors_count = int(m.group(1))

    # exit_code==1（テスト失敗）で failures==0 のときのみ補正
    if exit_code == 1 and failures == 0:
        failures = 1
    # exit_code==2（収集エラー）では failures を補正しない。errors_count を最低1保証
    if exit_code == 2 and errors_count == 0:
        errors_count = 1

    # headline: エラー原因が分かる1行
    headline = _extract_headline(lines) if lines else ""

    # error_lines: ERROR/E行を最大10行
    error_lines = _extract_error_lines(lines)

    # error_blocks: ファイル単位のエラーブロック（収集エラー分割用）
    error_blocks = _extract_error_blocks(lines) if exit_code == 2 and lines else []

    # failure_nodes: FAILED/ERROR行からnodeid単位で抽出（テスト失敗分割用）
    failure_nodes = _extract_failure_nodes(lines) if exit_code == 1 and lines else []

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
        "error_blocks": error_blocks,
        # v0.5.0 追加
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
        """workflow_lintを実行し結果を返す。

        Args:
            severity_filter: 取り込む重要度レベル（デフォルト: ERROR のみ）。
                例: ("[ERROR]", "[CAUTION]") で CAUTION も対象にする。
        """
        lint_script = self.workspace / "tools" / "workflow_lint.py"
        if not lint_script.exists():
            return {"available": False, "errors": 0, "findings": []}

        try:
            result = subprocess.run(
                [sys.executable, str(lint_script)],
                capture_output=True, text=True, timeout=60,
                cwd=str(self.workspace),
            )
            # severity_filter に合致する行を抽出
            findings = [
                line.strip() for line in result.stdout.splitlines()
                if any(line.strip().startswith(sev) for sev in severity_filter)
            ]
            return {
                "available": True,
                "errors": len(findings),
                "findings": findings[:20],  # 上限20件
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
        parts.extend(error_lines[:5])  # 最大5行

    desc = "\n".join(parts)
    return desc[:800]  # 長すぎ防止


def generate_candidates(scan_results: dict[str, Any]) -> list[dict[str, Any]]:
    """スキャン結果からタスク候補を生成する。"""
    candidates = []
    # workflow_lint の ERROR → 優先度1（stable_id使用）
    lint_data = scan_results.get("workflow_lint", {})
    for finding in lint_data.get("findings", []):
        task_id = _stable_task_id("lint", finding[:200])
        candidates.append({
            "task_id": task_id,
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
    error_blocks = pytest_data.get("error_blocks", [])
    failure_nodes = pytest_data.get("failure_nodes", [])

    # 収集エラー: error_blocks があればファイル単位に分割
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
        # error_blocks が取れないフォールバック
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

    # v0.5.0: テスト失敗を nodeid 単位で分割
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
        # failure_nodes が取れないフォールバック（従来互換）
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
        # errors_countもfailuresも取れない異常終了
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
    """候補に auto_fixable / blocked_reason を付与する。

    元のリストを変更して返す（コピーではない）。
    """
    for c in candidates:
        source = c.get("source", "")
        fixable = True
        reason = ""

        if source == "pytest":
            # target_nodeid も target_path もない → 総当たり候補
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


# ============================================================
# 失敗ログ管理
# ============================================================

def record_failure(state: dict, task_id: str, category: str, error: str) -> bool:
    """失敗をstateに記録し、3回でPAUSEDにする。

    Returns:
        bool: True = この呼び出しで paused_tasks に追加された（即停止すべき）
    """
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
        return True  # この瞬間に PAUSED 入り
    return False



# ============================================================
# KI Learning 記録
# ============================================================

def _record_ki(outcome: str, *, metadata: Optional[dict] = None, **kwargs) -> None:
    """構造化KI Learning記録。

    Args:
        outcome: SUCCESS / FAILURE / PARTIAL
        metadata: 構造化データ（failure_class, diff_summary, verification_result等）
        **kwargs: report_action_outcomeに渡す追加引数
    """
    if not _HAS_KI:
        return
    try:
        extra = {}
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


def _send_webhook(url: str, payload: dict) -> None:
    """サイクル結果をWebhook（Discord/Slack互換）で通知する。"""
    if not url:
        return
    try:
        import urllib.request
        data = json.dumps({"content": payload.get("summary", ""), "embeds": [{"title": "AGI Kernel", "description": json.dumps(payload, ensure_ascii=False, indent=2)[:2000]}]}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
        logger.info("[WEBHOOK] 通知送信成功")
    except Exception as e:
        logger.warning(f"[WEBHOOK] 通知失敗: {e}")


# ============================================================
# Gemini SDK 遅延インポート
# ============================================================

class _GeminiClientCompat:
    """google-genai / google-generativeai の互換ラッパー。"""

    def __init__(self, backend: str, client: Any):
        self.backend = backend
        self.client = client

    def generate_content(self, model_name: str, prompt: str):
        if self.backend == "google-genai":
            return self.client.models.generate_content(model=model_name, contents=prompt)
        model = self.client.GenerativeModel(model_name)
        return model.generate_content(prompt)


def _get_genai_client() -> _GeminiClientCompat:
    """Gemini SDKを遅延importする。google-genai優先、旧SDKへフォールバック。"""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("環境変数 GOOGLE_API_KEY / GEMINI_API_KEY が未設定です")

    try:
        # 新SDK (推奨)
        from google import genai as genai_sdk  # type: ignore
        client = genai_sdk.Client(api_key=api_key)
        return _GeminiClientCompat("google-genai", client)
    except ImportError:
        pass

    try:
        # 旧SDK (互換フォールバック)
        import warnings
        import google.generativeai as legacy_genai  # type: ignore

        warnings.filterwarnings(
            "ignore", category=FutureWarning, module=r"google\.generativeai"
        )
        legacy_genai.configure(api_key=api_key)
        logger.warning("[SDK] google-generativeai（旧SDK）を使用中。google-genai への移行を推奨します")
        return _GeminiClientCompat("google-generativeai", legacy_genai)
    except ImportError as exc:
        raise RuntimeError("google-genai / google-generativeai がインストールされていません") from exc


def _log_token_usage(response, model_name: str, state: Optional[dict] = None) -> dict[str, Any]:
    """トークン消費をログ出力し、stateに累積する。コスト推定も計算。"""
    usage: dict[str, Any] = {"prompt": 0, "output": 0, "total": 0, "estimated_cost_usd": 0.0}
    try:
        meta = response.usage_metadata
        usage["prompt"] = getattr(meta, "prompt_token_count", 0) or 0
        usage["output"] = getattr(meta, "candidates_token_count", 0) or 0
        usage["total"] = getattr(meta, "total_token_count", 0) or 0
        # コスト推定
        cost_rates = _COST_PER_1M.get(model_name, {"input": 0.15, "output": 0.60})
        usage["estimated_cost_usd"] = round(
            usage["prompt"] * cost_rates["input"] / 1_000_000
            + usage["output"] * cost_rates["output"] / 1_000_000, 6
        )
        logger.info(f"[TOKEN] model={model_name} input={usage['prompt']} output={usage['output']} total={usage['total']} cost=${usage['estimated_cost_usd']}")
    except (AttributeError, TypeError):
        logger.info("[TOKEN] トークン情報取得不可")

    # state に累積
    if state is not None:
        tu = state.setdefault("token_usage", {"prompt": 0, "output": 0, "total": 0, "estimated_cost_usd": 0.0})
        tu["prompt"] += usage["prompt"]
        tu["output"] += usage["output"]
        tu["total"] += usage["total"]
        tu["estimated_cost_usd"] = round(tu["estimated_cost_usd"] + usage["estimated_cost_usd"], 6)
    return usage


def _extract_response_text(response: Any) -> str:
    """Geminiレスポンスからテキストを抽出する。"""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    # 互換: candidates[].content.parts[].text
    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if isinstance(parts, list):
                for part in parts:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        return part_text

    raise ValueError("LLMレスポンスに有効なテキストがありません")


def _retry_delay_seconds(attempt: int) -> float:
    """指数バックオフ + ジッターで待機秒を計算する。"""
    base = LLM_RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
    jitter = random.uniform(0.0, LLM_RETRY_JITTER_SECONDS)
    return min(LLM_RETRY_MAX_DELAY_SECONDS, base) + jitter


def _sleep_before_retry(attempt: int, total_attempts: int, model_name: str) -> None:
    """次のリトライまで待機する。最終試行では待機しない。"""
    if attempt >= total_attempts - 1:
        return
    delay = _retry_delay_seconds(attempt)
    print(f"[EXECUTE] リトライ待機 {delay:.2f}s (model={model_name})")
    time.sleep(delay)


# ============================================================
# Executor抽象 + GeminiExecutor
# ============================================================

class Executor(ABC):
    """タスク修正パッチを生成する抽象基底クラス。"""

    @abstractmethod
    def generate_patch(self, task: dict, context: str, workspace: Path) -> dict:
        """パッチ結果dictを返す。

        Returns:
            {"files": [{"path": str, "action": str, "content": str}],
             "explanation": str}
        """
        ...


_PATCH_PROMPT_TEMPLATE = textwrap.dedent("""\
    あなたはリポジトリの自動修正エージェントです。
    以下のタスクを修正するためのパッチをJSON形式で出力してください。

    ## タスク
    タイトル: {title}
    説明: {description}
    ソース: {source}

    ## コンテキスト（スキャン結果の詳細）
    {context}

    ## 出力形式（厳密に守ること）
    以下のJSON形式のみ出力してください。説明文やマークダウンは不要です。
    ```json
    {{
      "files": [
        {{
          "path": "リポジトリルートからの相対パス",
          "action": "create または modify",
          "content": "ファイル全体の内容"
        }}
      ],
      "explanation": "変更の説明（日本語）"
    }}
    ```

    ## 制約
    - 変更ファイル数は最大{max_files}個
    - リポジトリ外のパスは禁止
    - 既存コードのスタイルを維持
    - テストが通ることを最優先
""")


class GeminiExecutor(Executor):
    """Gemini API でパッチを生成する実装。flash→proフォールバック付き。"""

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        strong_model_name: str = "gemini-2.5-pro",
    ):
        self.model_name = (
            os.environ.get("AGI_KERNEL_LLM_MODEL") or model_name
        )
        self.strong_model_name = (
            os.environ.get("AGI_KERNEL_LLM_STRONG_MODEL") or strong_model_name
        )

    def generate_patch(self, task: dict, context: str, workspace: Path) -> dict:
        """Gemini API でパッチを生成し、バリデーション済dictを返す。"""
        client = _get_genai_client()
        print(f"[EXECUTE] Gemini SDK backend={client.backend}")

        prompt = _PATCH_PROMPT_TEMPLATE.format(
            title=task.get("title", ""),
            description=task.get("description", ""),
            source=task.get("source", ""),
            context=context[:3000],
            max_files=MAX_PATCH_FILES,
        )

        # フェーズ1: 通常モデルで試行
        last_error = ""
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = client.generate_content(self.model_name, prompt)
                raw = _extract_response_text(response)
                # トークン消費ログ
                _log_token_usage(response, self.model_name)
                patch = _parse_patch_json(raw)
                _validate_patch_result(patch, workspace)
                return patch
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = str(e)
                print(f"[EXECUTE] LLMバリデーション失敗 (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.model_name)
                continue
            except Exception as e:
                last_error = str(e)
                print(f"[EXECUTE] LLM呼び出しエラー (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.model_name)
                continue

        # フェーズ2: 強力モデルでフォールバック
        if self.strong_model_name != self.model_name:
            print(f"[EXECUTE] → 強力モデル({self.strong_model_name})でフォールバック...")
            for attempt in range(MAX_LLM_RETRIES):
                try:
                    response = client.generate_content(self.strong_model_name, prompt)
                    raw = _extract_response_text(response)
                    _log_token_usage(response, self.strong_model_name)
                    patch = _parse_patch_json(raw)
                    _validate_patch_result(patch, workspace)
                    return patch
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    last_error = str(e)
                    print(f"[EXECUTE] 強力モデルバリデーション失敗 (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                    _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.strong_model_name)
                    continue
                except Exception as e:
                    last_error = str(e)
                    print(f"[EXECUTE] 強力モデルエラー (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                    _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.strong_model_name)
                    continue

        raise RuntimeError(f"LLMパッチ生成に失敗: {last_error}")


def _collect_json_candidates(raw: str) -> list[str]:
    """LLM出力からJSON候補文字列を順序付きで収集する。"""
    text = raw.strip()
    if not text:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        normalized = candidate.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    # 1) fenced code block
    for m in re.finditer(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL):
        _append(m.group(1))

    # 2) 全体
    _append(text)

    # 3) 中括弧バランスの取れた部分文字列を走査
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
            continue
        if ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                _append(text[start:idx + 1])
                start = -1

    return candidates


def _parse_patch_json(raw: str) -> dict:
    """LLM出力からJSON部分を抽出してパースする。"""
    last_error: Optional[json.JSONDecodeError] = None
    for candidate in _collect_json_candidates(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
            continue
        if isinstance(parsed, dict):
            return parsed

    if last_error is not None:
        raise json.JSONDecodeError("Valid JSON object not found in LLM output", raw, 0)
    raise json.JSONDecodeError("JSON not found in LLM output", raw, 0)


def _validate_patch_result(patch: dict, workspace: Path) -> None:
    """パッチ結果をバリデーションする。失敗時はValueError。"""
    if not isinstance(patch, dict):
        raise ValueError("パッチ結果がdictではありません")
    files = patch.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError("files が空またはリストではありません")
    if len(files) > MAX_PATCH_FILES:
        raise ValueError(f"変更ファイル数 {len(files)} > 上限 {MAX_PATCH_FILES}")

    ws_resolved = workspace.resolve()
    for f in files:
        if not isinstance(f, dict):
            raise ValueError("files の各要素はdictである必要があります")
        path_str = f.get("path", "")
        if not path_str:
            raise ValueError("path が空です")
        # パス安全検証: トラバーサル禁止、workspace 配下であること
        # os.sep で分割して「..」コンポーネントを検出（foo..bar.py 等の誤検出防止）
        path_parts = Path(path_str).parts
        if ".." in path_parts:
            raise ValueError(f"path に '..' が含まれています: {path_str}")
        resolved = (workspace / path_str).resolve()
        # Path.is_relative_to で原子的に検証（startswith文字列比較の回避）
        try:
            resolved.relative_to(ws_resolved)
        except ValueError:
            raise ValueError(f"path がワークスペース外です: {path_str}")
        action = f.get("action", "")
        if action not in ("create", "modify"):
            raise ValueError(f"action が不正です: {action}")
        if "content" not in f:
            raise ValueError(f"content がありません: {path_str}")


def _apply_patch(patch: dict, workspace: Path) -> list[Path]:
    """パッチをファイルシステムに適用する。変更したパスのリストを返す。"""
    modified_paths: list[Path] = []
    for f in patch["files"]:
        target = workspace / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8")
        modified_paths.append(target)
    return modified_paths


# ============================================================
# Preflight / Backup / Rollback / Diff（v0.3.1 安全強化）
# ============================================================

def _preflight_check(workspace: Path) -> dict:
    """EXECUTE前の安全チェック。

    Returns:
        {"ok": bool, "reason": str, "git_available": bool}
    """
    # git 利用可能か
    try:
        v = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if v.returncode != 0:
            return {"ok": True, "reason": "", "git_available": False}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # git が無い環境 → diff は difflib で担保するため通す
        return {"ok": True, "reason": "", "git_available": False}

    # git リポジトリ内か
    try:
        rp = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=5,
        )
        if rp.returncode != 0:
            return {"ok": True, "reason": "", "git_available": False}
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": True, "reason": "", "git_available": False}

    # 作業ツリーがクリーンか
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=10,
        )
        porcelain = st.stdout.strip()
        if porcelain:
            return {"ok": False, "reason": "dirty_worktree", "git_available": True}
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "reason": "git_status_failed", "git_available": True}

    return {"ok": True, "reason": "", "git_available": True}


def _backup_targets(
    patch: dict, workspace: Path, backup_dir: Path,
) -> dict[str, Optional[Path]]:
    """パッチ適用前に変更対象ファイルのバックアップを作成する。

    Returns:
        {relative_path: backup_path or None (新規ファイル)}
    """
    backup_map: dict[str, Optional[Path]] = {}
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in patch["files"]:
        rel = f["path"]
        original = workspace / rel
        if original.exists():
            # 既存ファイル → バックアップ
            bak_path = backup_dir / rel
            bak_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(original), str(bak_path))
            backup_map[rel] = bak_path
        else:
            # 新規ファイル
            backup_map[rel] = None
    return backup_map


def _rollback_with_backup(
    modified_paths: list[Path],
    backup_map: dict[str, Optional[Path]],
    workspace: Path,
) -> None:
    """バックアップから復元する。新規ファイルは削除。

    git checkout は補助的に試行するが、主はバックアップ復元。
    """
    for p in modified_paths:
        try:
            rel = str(p.relative_to(workspace)).replace("\\", "/")
        except ValueError:
            continue

        bak = backup_map.get(rel)
        if bak is not None:
            # 既存ファイル → バックアップから復元
            try:
                shutil.copy2(str(bak), str(p))
            except OSError:
                # バックアップ復元失敗 → git checkout を試行
                try:
                    subprocess.run(
                        ["git", "checkout", "--", rel],
                        cwd=str(workspace),
                        capture_output=True, text=True, timeout=10,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass  # 最終手段なし — ログだけ残す
        else:
            # 新規ファイル → 削除
            if p.exists():
                p.unlink()


def _restore_rollback_context(
    state: dict, workspace: Path, output_dir: Path,
) -> tuple[list[Path], dict[str, Optional[Path]]]:
    """ステートからmodified_pathsとbackup_mapを復元する（RESUME安全性）。

    EXECUTE後にプロセスが落ちても、state.jsonから
    ロールバックに必要な情報を復元できる。
    """
    exec_result = state.get("execution_result", {})
    modified_files = exec_result.get("modified_files", [])
    backup_dir_rel = exec_result.get("backup_dir", "")

    modified_paths = [workspace / f for f in modified_files]
    backup_map: dict[str, Optional[Path]] = {}

    if backup_dir_rel:
        backup_dir = output_dir / backup_dir_rel
        for rel in modified_files:
            bak = backup_dir / rel
            if bak.exists():
                backup_map[rel] = bak
            else:
                backup_map[rel] = None  # 新規ファイル扱い

    return modified_paths, backup_map


def _compute_patch_diff_lines(
    patch: dict, backup_map: dict[str, Optional[Path]],
) -> int:
    """バックアップ vs パッチ内容で純粋な差分行数を算出する（git非依存）。"""
    total = 0
    for f in patch["files"]:
        rel = f["path"]
        new_content = f.get("content", "")
        new_lines = new_content.splitlines(keepends=True)

        bak = backup_map.get(rel)
        if bak is not None and bak.exists():
            # 既存ファイル → difflib で比較
            old_lines = bak.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        else:
            # 新規ファイル → 全行が追加
            old_lines = []

        diff = list(difflib.unified_diff(old_lines, new_lines, n=0))
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                total += 1
            elif line.startswith("-") and not line.startswith("---"):
                total += 1
    return total


def _build_execute_context(task: dict, scan_results: dict,
                           workspace: Path | None = None) -> str:
    """EXECUTEフェーズ用のLLMコンテキストを構築する。

    workspaceが指定されている場合、target_pathのファイル内容も含める。
    """
    parts = []
    source = task.get("source", "")
    if source == "pytest":
        pytest_data = scan_results.get("pytest", {})
        parts.append(f"pytest exit_code: {pytest_data.get('exit_code', '?')}")
        parts.append(f"pytest summary: {pytest_data.get('summary', '')}")
        headline = pytest_data.get("headline", "")
        if headline:
            parts.append(f"headline: {headline}")
        error_lines = pytest_data.get("error_lines", [])
        if error_lines:
            parts.append("error_lines:")
            parts.extend(f"  {line}" for line in error_lines[:10])
        tail = pytest_data.get("tail", [])
        if tail:
            parts.append("tail (last 20 lines):")
            parts.extend(f"  {line}" for line in tail)
    elif source == "workflow_lint":
        lint_data = scan_results.get("workflow_lint", {})
        parts.append(f"lint findings: {lint_data.get('findings', [])}")
    parts.append(f"task description: {task.get('description', '')}")

    # ターゲットファイルの内容を読み込んでコンテキストに追加
    target_path = task.get("target_path", "")
    if target_path and workspace:
        target_file = workspace / target_path
        if target_file.exists() and target_file.is_file():
            try:
                content = target_file.read_text(encoding="utf-8", errors="replace")
                # ファイル内容は最大4000文字まで
                if len(content) > 4000:
                    content = content[:4000] + "\n... (截端)"
                parts.append(f"\n--- target_path: {target_path} ---")
                parts.append(content)
            except OSError:
                pass

    # 依存関係情報を追加
    if workspace:
        for dep_file in ["requirements.txt", "pyproject.toml"]:
            dep_path = workspace / dep_file
            if dep_path.exists() and dep_path.is_file():
                try:
                    dep_content = dep_path.read_text(encoding="utf-8", errors="replace")
                    if len(dep_content) > 2000:
                        dep_content = dep_content[:2000] + "\n... (截端)"
                    parts.append(f"\n--- {dep_file} ---")
                    parts.append(dep_content)
                except OSError:
                    pass
                break  # どちらか1つあればOK

    # ディレクトリ構造を追加（浅い階層のみ）
    if workspace:
        try:
            tree_lines = []
            for p in sorted(workspace.rglob("*")):
                rel = p.relative_to(workspace)
                # 深さ制限 + 除外ディレクトリ
                parts_list = rel.parts
                if len(parts_list) > 3:
                    continue
                if any(x.startswith(".") or x in ("__pycache__", "node_modules", "_outputs", "_logs", ".venv", "venv")
                       for x in parts_list):
                    continue
                tree_lines.append(str(rel))
                if len(tree_lines) >= 50:
                    break
            if tree_lines:
                parts.append("\n--- directory structure (depth<=3) ---")
                parts.extend(tree_lines)
        except OSError:
            pass

    return "\n".join(parts)


# ============================================================
# Verifier
# ============================================================

class Verifier:
    """タスク種別に応じた検証コマンドを実行する。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def verify(self, task: dict) -> dict[str, Any]:
        """検証を実行し結果dictを返す。

        Returns:
            {"success": bool, "exit_code": int, "output": str, "command": str}
        """
        source = task.get("source", "")
        target_path = task.get("target_path", "")
        target_nodeid = task.get("target_nodeid", "")
        if source == "pytest" and target_nodeid:
            # v0.5.0: nodeid限定検証（最も狭い範囲）
            cmd = [sys.executable, "-m", "pytest", target_nodeid, "-q", "--tb=short", "--color=no"]
        elif source == "pytest" and target_path:
            # ターゲット限定検証
            cmd = [sys.executable, "-m", "pytest", target_path, "-q", "--tb=short", "--color=no"]
        elif source == "pytest":
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"]
        elif source == "workflow_lint":
            lint_script = self.workspace / "tools" / "workflow_lint.py"
            if lint_script.exists():
                cmd = [sys.executable, str(lint_script)]
            else:
                cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"]

        cmd_str = " ".join(cmd)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=120, cwd=str(self.workspace),
            )
            output = (result.stdout + result.stderr)[-2000:]  # 末尾2000文字
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "output": output,
                "command": cmd_str,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "output": "タイムアウト (120s)",
                "command": cmd_str,
            }
        except OSError as e:
            return {
                "success": False,
                "exit_code": -1,
                "output": str(e),
                "command": cmd_str,
            }


# ============================================================
# フェーズ順序定義（--resume 用）
# ============================================================

PHASE_ORDER = ["BOOT", "SCAN", "SENSE", "SELECT", "EXECUTE", "VERIFY", "LEARN", "CHECKPOINT"]


def _should_skip_phase(last_completed: str, target_phase: str) -> bool:
    """resume時、target_phaseが既に完了済みかを判定する。

    last_completed は「最後に完了したphase」。
    target_phase が last_completed 以前 → スキップ。
    target_phase が last_completed の次 → 再開（スキップしない）。
    """
    try:
        completed_idx = PHASE_ORDER.index(last_completed)
        target_idx = PHASE_ORDER.index(target_phase)
        # target が completed 以下 → スキップ（完了済み）
        return target_idx <= completed_idx
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
    resume_phase: Optional[str] = None  # last_completed_phase（resume判定用）

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
                # RUNNING → last_completed_phase の次から再開
                # 後方互換: last_completed_phase がなければ phase から推定
                resume_phase = state.get("last_completed_phase")
                if resume_phase is None:
                    # 旧形式: phase は「開始済み」なのでその1つ前を completed とみなす
                    old_phase = state.get("phase", "BOOT")
                    try:
                        idx = PHASE_ORDER.index(old_phase)
                        resume_phase = PHASE_ORDER[idx - 1] if idx > 0 else None
                    except ValueError:
                        resume_phase = None
                if resume_phase:
                    print(f"[BOOT] last_completed_phase={resume_phase} の次から再開します。")
                else:
                    print("[BOOT] 完了済みフェーズなし。最初から実行します。")
    else:
        state = sm.new_state()

    state["phase"] = "BOOT"
    state["last_completed_phase"] = None
    state["status"] = "RUNNING"
    date_str = datetime.now(JST).strftime("%Y%m%d")

    print(f"[BOOT] サイクル開始: cycle_id={state['cycle_id']}")
    state["last_completed_phase"] = "BOOT"
    sm.save(state)  # BOOT checkpoint

    # ── SCAN ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SCAN")):
        state["phase"] = "SCAN"
        print("[SCAN] リポジトリスキャン中...")
        scanner = Scanner(workspace)
        # v0.6.0: --lint-severity 対応
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
        print(f"[SCAN] lint_errors={_lint_errors}, pytest_errors={_pytest_errors}, pytest_failures={_pytest_failures}")
        state["last_completed_phase"] = "SCAN"
        sm.save(state)  # SCAN checkpoint
    else:
        print("[SCAN] resume: スキップ（完了済み）")

    # ── SENSE ──
    if not (resume_phase and _should_skip_phase(resume_phase, "SENSE")):
        state["phase"] = "SENSE"
        candidates = generate_candidates(state["scan_results"])
        state["candidates"] = candidates
        # v0.5.0: auto_fixable アノテーション
        annotate_candidates(candidates)
        sm.save_candidates(candidates, date_str, state["cycle_id"])
        print(f"[SENSE] タスク候補: {len(candidates)}件")
        blocked = [c for c in candidates if not c.get("auto_fixable", True)]
        fixable = [c for c in candidates if c.get("auto_fixable", True)]
        if blocked:
            print(f"[SENSE] auto_fixable=false: {len(blocked)}件 (blocked)")
        print(f"[SENSE] auto_fixable=true: {len(fixable)}件 (対処可能)")
        state["last_completed_phase"] = "SENSE"
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
            # v0.5.0: blocked候補の集計
            blocked = [c for c in candidates if not c.get("auto_fixable", True)]
            reason = "no_fixable_candidates" if blocked else "no_candidates"
            print(f"[SELECT] 対処可能なタスクがありません（{reason}）。サイクル完了。")
            state["status"] = "COMPLETED"
            state["completed_at"] = datetime.now(JST).isoformat()
            state["phase"] = "CHECKPOINT"
            state["last_completed_phase"] = "CHECKPOINT"
            sm.save(state)
            # 候補なし早期終了でも report.json を出力
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
            report_path = sm.save_report(report, date_str, state["cycle_id"])
            _record_ki("SUCCESS", cycle_id=state["cycle_id"], task_id="none", note=reason)
            print(f"[CHECKPOINT] state保存完了: {sm.state_path}")
            print(f"[CHECKPOINT] レポート出力: {report_path}")
            return 0
        print(f"[SELECT] タスク選択: {selected['task_id']} — {selected['title']}")
        state["last_completed_phase"] = "SELECT"
        sm.save(state)  # SELECT checkpoint
    else:
        print("[SELECT] resume: スキップ（完了済み）")
        selected = state.get("selected_task")

    # ── EXECUTE ──
    modified_paths: list[Path] = []  # ロールバック用
    backup_map: dict[str, Optional[Path]] = {}  # バックアップマップ
    if not (resume_phase and _should_skip_phase(resume_phase, "EXECUTE")):
        state["phase"] = "EXECUTE"
        if args.dry_run:
            print("[EXECUTE] dry-runモード: スキップ")
            state["execution_result"] = {"dry_run": True, "skipped": True}
        else:
            # ── Preflight ──
            preflight = _preflight_check(workspace)
            if not preflight["ok"]:
                reason = preflight["reason"]
                print(f"[EXECUTE] ❌ Preflight失敗 (環境ブロッカー): {reason}")
                # v0.5.0: 環境ブロッカーはfailure_logに積まず即PAUSED
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
                report_path = sm.save_report(report, date_str, state["cycle_id"])
                _record_ki("FAILURE", cycle_id=state["cycle_id"],
                           task_id=selected["task_id"] if selected else "none",
                           note=f"env_blocker:{reason}")
                print(f"[CHECKPOINT] レポート出力: {report_path}")
                return 1  # 非0終了
            else:
                if not preflight["git_available"]:
                    print("[EXECUTE] ⚠️ git不在 — difflibベースで安全弁を適用")

                # ── LLMパッチ生成→バックアップ→適用→diff検証 ──
                print("[EXECUTE] LLMパッチ生成を開始...")
                try:
                    # CLIフラグでモデルを指定可能
                    model_name = getattr(args, "llm_model", None) or "gemini-2.5-flash"
                    strong_name = getattr(args, "llm_strong_model", None) or "gemini-2.5-pro"
                    executor = GeminiExecutor(
                        model_name=model_name,
                        strong_model_name=strong_name,
                    )
                    context = _build_execute_context(selected, state["scan_results"], workspace)
                    patch = executor.generate_patch(selected, context, workspace)
                    print(f"[EXECUTE] パッチ生成完了: {len(patch['files'])}ファイル")
                    print(f"[EXECUTE] 説明: {patch.get('explanation', '')[:200]}")

                    # バックアップ作成
                    date_str = datetime.now(JST).strftime("%Y%m%d")
                    backup_dir = output_dir / date_str / state["cycle_id"] / "backup"
                    backup_map = _backup_targets(patch, workspace, backup_dir)
                    print(f"[EXECUTE] バックアップ完了: {backup_dir}")

                    # v0.6.0: --approve ゲート
                    if getattr(args, "approve", False):
                        print("="*60)
                        print("[APPROVE] パッチ内容:")
                        for f in patch["files"]:
                            print(f"  {f.get('action', 'modify')}: {f['path']}")
                        print(f"  説明: {patch.get('explanation', '')[:300]}")
                        print("="*60)
                        answer = input("[APPROVE] 適用しますか? (y/n): ").strip().lower()
                        if answer != "y":
                            print("[APPROVE] ユーザーが拒否。スキップ。")
                            state["execution_result"] = {"success": False, "error": "user_rejected"}
                            state["last_completed_phase"] = "EXECUTE"
                            sm.save(state)
                            modified_paths = []
                            # VERIFYもスキップしてLEARNへ
                            resume_phase = "EXECUTE"  # VERIFYをスキップさせる
                            state["verification_result"] = {"success": False, "skipped": True}
                            state["last_completed_phase"] = "VERIFY"
                            sm.save(state)
                            # LEARN/CHECKPOINTへ落ちる

                    # パッチ適用
                    modified_paths = _apply_patch(patch, workspace)
                    print(f"[EXECUTE] パッチ適用完了: {[str(p.relative_to(workspace)) for p in modified_paths]}")

                    # diff行数チェック（difflibベース — git非依存）
                    diff_lines = _compute_patch_diff_lines(patch, backup_map)
                    print(f"[EXECUTE] diff行数: {diff_lines}")
                    if diff_lines > MAX_DIFF_LINES:
                        print(f"[EXECUTE] diff行数 {diff_lines} > 上限 {MAX_DIFF_LINES}。ロールバックします。")
                        _rollback_with_backup(modified_paths, backup_map, workspace)
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
                            # RESUME安全性: ロールバック情報を永続化
                            "modified_files": [
                                str(p.relative_to(workspace)).replace("\\", "/")
                                for p in modified_paths
                            ],
                            "backup_dir": str(
                                backup_dir.relative_to(output_dir)
                            ).replace("\\", "/"),
                        }
                except RuntimeError as e:
                    print(f"[EXECUTE] エラー: {e}")
                    if modified_paths:
                        _rollback_with_backup(modified_paths, backup_map, workspace)
                        modified_paths = []
                    state["execution_result"] = {"success": False, "error": str(e)}
                except Exception as e:
                    print(f"[EXECUTE] 予期しないエラー: {e}")
                    if modified_paths:
                        _rollback_with_backup(modified_paths, backup_map, workspace)
                        modified_paths = []
                    state["execution_result"] = {"success": False, "error": str(e)}

        state["last_completed_phase"] = "EXECUTE"
        sm.save(state)  # EXECUTE checkpoint
    else:
        print("[EXECUTE] resume: スキップ（完了済み）")

    # ── VERIFY ──
    if not (resume_phase and _should_skip_phase(resume_phase, "VERIFY")):
        state["phase"] = "VERIFY"
        exec_result = state.get("execution_result", {})

        # RESUME安全性: modified_pathsが空でもstateから復元
        if not modified_paths and exec_result.get("modified_files"):
            modified_paths, backup_map = _restore_rollback_context(
                state, workspace, output_dir,
            )
            if modified_paths:
                print(f"[VERIFY] ロールバックコンテキストをstateから復元 ({len(modified_paths)}ファイル)")

        if args.dry_run:
            print("[VERIFY] dry-runモード: スキップ")
            state["verification_result"] = {"dry_run": True, "skipped": True}
        elif not exec_result.get("success", False):
            print("[VERIFY] EXECUTE失敗のためスキップ")
            state["verification_result"] = {"skipped": True, "reason": "execute_failed"}
        else:
            print("[VERIFY] 検証コマンドを実行中...")
            verifier = Verifier(workspace)
            verify_result = verifier.verify(selected)
            state["verification_result"] = verify_result
            if verify_result["success"]:
                print(f"[VERIFY] ✅ 検証成功 (exit_code={verify_result['exit_code']})")
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
                        print("[VERIFY] 🔒 auto-commit 完了")
                        state["verification_result"]["auto_committed"] = True
                    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as ce:
                        print(f"[VERIFY] ⚠️ auto-commit 失敗: {ce}")
                elif exec_git:
                    print("[VERIFY] ⚠️ VERIFY成功。次サイクル安定化のため手動commitを推奨します。")
            else:
                print(f"[VERIFY] ❌ 検証失敗 (exit_code={verify_result['exit_code']})")
                print(f"[VERIFY] 出力: {verify_result['output'][:500]}")
                if modified_paths:
                    print("[VERIFY] 変更をロールバックします...")
                    _rollback_with_backup(modified_paths, backup_map, workspace)
                    state["verification_result"]["rolled_back"] = True

        state["last_completed_phase"] = "VERIFY"
        sm.save(state)  # VERIFY checkpoint
    else:
        print("[VERIFY] resume: スキップ（完了済み）")

    # ── LEARN ──
    paused_now = False
    if not (resume_phase and _should_skip_phase(resume_phase, "LEARN")):
        state["phase"] = "LEARN"

        # outcome 判定（v0.6.0: メタデータ用変数を事前初期化）
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
            # failure_log に記録
            error_msg = verify_result.get("output", "verification failed")[:500]
            category = classify_failure(error_msg)
            paused_now = record_failure(state, selected["task_id"], category, error_msg)
        else:
            outcome = "FAILURE"
            note = "execute_failed"
            error_msg = exec_result.get("error", "execute failed")[:500]
            category = classify_failure(error_msg)
            paused_now = record_failure(state, selected["task_id"], category, error_msg)

        _record_ki(
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
        print(f"[LEARN] KI Learning記録: outcome={outcome}, note={note}")
        state["last_completed_phase"] = "LEARN"
        sm.save(state)  # LEARN checkpoint
    else:
        print("[LEARN] resume: スキップ（完了済み）")
        outcome = state.get("execution_result", {}).get("success", False) and \
                  state.get("verification_result", {}).get("success", False)
        outcome = "SUCCESS" if outcome else "PARTIAL"
        if selected and selected.get("task_id") in state.get("paused_tasks", []):
            paused_now = True

    # ── CHECKPOINT ──
    state["phase"] = "CHECKPOINT"
    state["last_completed_phase"] = "CHECKPOINT"
    state["completed_at"] = datetime.now(JST).isoformat()

    # v0.5.2: paused_now は locals() 参照せず明示変数で管理
    paused_now_flag = paused_now
    if paused_now_flag:
        state["status"] = "PAUSED"
        print(f"[CHECKPOINT] ⚠️ タスク {selected['task_id']} が {MAX_TASK_FAILURES}回失敗 → PAUSED停止")
    else:
        state["status"] = "COMPLETED"
    sm.save(state)

    # レポート出力（state.status と一致した状態で生成）
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
    report_path = sm.save_report(report, date_str, state["cycle_id"])
    print(f"[CHECKPOINT] state保存完了: {sm.state_path}")
    print(f"[CHECKPOINT] レポート出力: {report_path}")

    # v0.6.0: Webhook通知
    webhook_url = getattr(args, "webhook_url", None)
    if webhook_url:
        _send_webhook(webhook_url, {
            "summary": f"AGI Kernel: cycle={state['cycle_id']} status={state['status']} outcome={outcome}",
            "cycle_id": state["cycle_id"],
            "status": state["status"],
            "outcome": outcome,
            "token_usage": state.get("token_usage", {}),
        })

    return 1 if paused_now_flag else 0


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AGI Kernel — 自己改善ループ",
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

    if args.loop:
        # 常駐モード
        logger.info(f"[KERNEL] 常駐モード開始 (interval={args.interval}s)")
        cycle_count = 0
        try:
            while True:
                cycle_count += 1
                logger.info(f"[KERNEL] === サイクル #{cycle_count} 開始 ===")
                exit_code = run_cycle(args)
                if exit_code != 0:
                    logger.info(f"[KERNEL] サイクル #{cycle_count} が exit_code={exit_code} で終了。ループ停止。")
                    return exit_code
                logger.info(f"[KERNEL] サイクル #{cycle_count} 完了。{args.interval}秒後に次のサイクル...")
                # resume フラグをリセットして新規サイクルへ
                args.resume = False
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info(f"[KERNEL] Ctrl+C を受信。{cycle_count}サイクル実行後に終了。")
            return 0
    else:
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

