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
import re
from abc import ABC, abstractmethod
import textwrap

__version__ = "0.3.1"

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

# ── EXECUTE/VERIFY 安全制限 ──
MAX_PATCH_FILES = 5          # 1回のパッチで変更可能な最大ファイル数
MAX_DIFF_LINES = 200         # 1回のパッチの最大diff行数
MAX_LLM_RETRIES = 3          # LLM出力バリデーション失敗時の最大リトライ
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
                [sys.executable, "-m", "pytest", "-q", "--tb=no", "--color=no"],
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

    if errors_count > 0:
        title = f"収集エラー修正 ({errors_count}件)"
        candidates.append({
            "task_id": f"pytest_exit_{pytest_exit}",
            "source": "pytest",
            "priority": 2,
            "title": title,
            "description": description,
            "estimated_effort": "medium",
        })
    if pytest_failures > 0:
        title = f"テスト失敗修正 ({pytest_failures}件)"
        candidates.append({
            "task_id": f"pytest_exit_{pytest_exit}",
            "source": "pytest",
            "priority": 2,
            "title": title,
            "description": description,
            "estimated_effort": "medium",
        })
    elif pytest_exit not in (0, None) and pytest_exit != -1 and errors_count == 0:
        # errors_countもfailuresも取れない異常終了
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
# Gemini SDK 遅延インポート
# ============================================================

def _get_genai():
    """google.generativeai を遅延importする（未インストールでも起動可能）。"""
    try:
        import google.generativeai as genai
        import warnings
        warnings.filterwarnings("ignore", category=FutureWarning)
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("環境変数 GOOGLE_API_KEY が未設定です")
        genai.configure(api_key=api_key)
        return genai
    except ImportError:
        raise RuntimeError("google-generativeai がインストールされていません")


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
    """Gemini API でパッチを生成する実装。"""

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = model_name

    def generate_patch(self, task: dict, context: str, workspace: Path) -> dict:
        """Gemini API でパッチを生成し、バリデーション済みdictを返す。"""
        genai = _get_genai()
        model = genai.GenerativeModel(self.model_name)

        prompt = _PATCH_PROMPT_TEMPLATE.format(
            title=task.get("title", ""),
            description=task.get("description", ""),
            source=task.get("source", ""),
            context=context[:3000],  # コンテキストは3000文字まで
            max_files=MAX_PATCH_FILES,
        )

        last_error = ""
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = model.generate_content(prompt)
                raw = response.text
                patch = _parse_patch_json(raw)
                _validate_patch_result(patch, workspace)
                return patch
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = str(e)
                print(f"[EXECUTE] LLMバリデーション失敗 (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                continue
            except Exception as e:
                last_error = str(e)
                print(f"[EXECUTE] LLM呼び出しエラー (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                continue

        raise RuntimeError(f"LLMパッチ生成に{MAX_LLM_RETRIES}回失敗: {last_error}")


def _parse_patch_json(raw: str) -> dict:
    """LLM出力からJSON部分を抽出してパースする。"""
    # ```json ... ``` ブロックを探す
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # ブロックなしの場合、全体をJSONとしてパース
    # 先頭/末尾の非JSONテキストを除去
    stripped = raw.strip()
    start = stripped.find('{')
    end = stripped.rfind('}') + 1
    if start >= 0 and end > start:
        return json.loads(stripped[start:end])
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
        # パス安全検証: .. 禁止、workspace 配下であること
        if ".." in path_str:
            raise ValueError(f"path に '..' が含まれています: {path_str}")
        resolved = (workspace / path_str).resolve()
        if not str(resolved).startswith(str(ws_resolved)):
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


def _build_execute_context(task: dict, scan_results: dict) -> str:
    """EXECUTEフェーズ用のLLMコンテキストを構築する。"""
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
        if source == "pytest":
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"]
        elif source == "workflow_lint":
            lint_script = self.workspace / "tools" / "workflow_lint.py"
            if lint_script.exists():
                cmd = [sys.executable, str(lint_script)]
            else:
                # lint スクリプトなし → pytest で代替
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
        lint_result = scanner.run_workflow_lint()
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
        sm.save_candidates(candidates, date_str, state["cycle_id"])
        print(f"[SENSE] タスク候補: {len(candidates)}件")
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
            print("[SELECT] 対処可能なタスクがありません。サイクル完了。")
            state["status"] = "COMPLETED"
            state["completed_at"] = datetime.now(JST).isoformat()
            state["phase"] = "CHECKPOINT"
            state["last_completed_phase"] = "CHECKPOINT"
            sm.save(state)
            # 候補なし早期終了でも report.json を出力
            report = {
                "cycle_id": state["cycle_id"],
                "status": state["status"],
                "reason": "no_candidates",
                "scan_summary": {
                    "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
                    "pytest_errors": state["scan_results"].get("pytest_errors", 0),
                    "pytest_failures": state["scan_results"].get("pytest_failures", 0),
                },
                "candidates_count": 0,
                "selected_task": None,
                "outcome": "SUCCESS",
                "paused_tasks": state.get("paused_tasks", []),
            }
            report_path = sm.save_report(report, date_str, state["cycle_id"])
            _record_ki("SUCCESS", cycle_id=state["cycle_id"], task_id="none", note="no_candidates")
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
                print(f"[EXECUTE] ❌ Preflight失敗: {reason}")
                state["execution_result"] = {
                    "success": False,
                    "error": f"preflight_failed: {reason}",
                    "reason": reason,
                }
            else:
                if not preflight["git_available"]:
                    print("[EXECUTE] ⚠️ git不在 — difflibベースで安全弁を適用")

                # ── LLMパッチ生成→バックアップ→適用→diff検証 ──
                print("[EXECUTE] LLMパッチ生成を開始...")
                try:
                    executor = GeminiExecutor(model_name="gemini-2.5-flash")
                    context = _build_execute_context(selected, state["scan_results"])
                    patch = executor.generate_patch(selected, context, workspace)
                    print(f"[EXECUTE] パッチ生成完了: {len(patch['files'])}ファイル")
                    print(f"[EXECUTE] 説明: {patch.get('explanation', '')[:200]}")

                    # バックアップ作成
                    date_str = datetime.now(JST).strftime("%Y%m%d")
                    backup_dir = output_dir / date_str / state["cycle_id"] / "backup"
                    backup_map = _backup_targets(patch, workspace, backup_dir)
                    print(f"[EXECUTE] バックアップ完了: {backup_dir}")

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

        if args.dry_run:
            print("[VERIFY] dry-runモード: スキップ")
            state["verification_result"] = {"dry_run": True, "skipped": True}
        elif not exec_result.get("success", False):
            # EXECUTE が失敗 → VERIFY もスキップ
            print("[VERIFY] EXECUTE失敗のためスキップ")
            state["verification_result"] = {"skipped": True, "reason": "execute_failed"}
        else:
            # Verifier で再テスト
            print("[VERIFY] 検証コマンドを実行中...")
            verifier = Verifier(workspace)
            verify_result = verifier.verify(selected)
            state["verification_result"] = verify_result
            if verify_result["success"]:
                print(f"[VERIFY] ✅ 検証成功 (exit_code={verify_result['exit_code']})")
                # auto-commit / 警告
                exec_git = state.get("execution_result", {}).get("git_available", False)
                if getattr(args, "auto_commit", False) and exec_git:
                    try:
                        subprocess.run(
                            ["git", "add", "-A"],
                            cwd=str(workspace),
                            capture_output=True, text=True, timeout=10,
                        )
                        task_id = selected.get("id", "unknown") if selected else "unknown"
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
                # 検証失敗 → ロールバック（バックアップ復元）
                if modified_paths:
                    print("[VERIFY] 変更をロールバックします...")
                    _rollback_with_backup(modified_paths, backup_map, workspace)
                    state["verification_result"]["rolled_back"] = True

        state["last_completed_phase"] = "VERIFY"
        sm.save(state)  # VERIFY checkpoint
    else:
        print("[VERIFY] resume: スキップ（完了済み）")

    # ── LEARN ──
    if not (resume_phase and _should_skip_phase(resume_phase, "LEARN")):
        state["phase"] = "LEARN"

        # outcome 判定
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
            record_failure(state, selected["task_id"], category, error_msg)
        else:
            outcome = "FAILURE"
            note = "execute_failed"
            error_msg = exec_result.get("error", "execute failed")[:500]
            category = classify_failure(error_msg)
            record_failure(state, selected["task_id"], category, error_msg)

        _record_ki(
            outcome=outcome,
            cycle_id=state["cycle_id"],
            task_id=selected["task_id"] if selected else "none",
            note=note,
        )
        print(f"[LEARN] KI Learning記録: outcome={outcome}, note={note}")
        state["last_completed_phase"] = "LEARN"
        sm.save(state)  # LEARN checkpoint
    else:
        print("[LEARN] resume: スキップ（完了済み）")
        outcome = state.get("execution_result", {}).get("success", False) and \
                  state.get("verification_result", {}).get("success", False)
        outcome = "SUCCESS" if outcome else "PARTIAL"

    # ── CHECKPOINT ──
    state["phase"] = "CHECKPOINT"
    state["last_completed_phase"] = "CHECKPOINT"
    state["status"] = "COMPLETED"
    state["completed_at"] = datetime.now(JST).isoformat()
    sm.save(state)
    # レポート出力
    report = {
        "cycle_id": state["cycle_id"],
        "status": state["status"],
        "scan_summary": {
            "lint_errors": state["scan_results"].get("workflow_lint_errors", 0),
            "pytest_errors": state["scan_results"].get("pytest_errors", 0),
            "pytest_failures": state["scan_results"].get("pytest_failures", 0),
        },
        "candidates_count": len(candidates),
        "selected_task": selected,
        "outcome": outcome,
        "paused_tasks": state.get("paused_tasks", []),
    }
    report_path = sm.save_report(report, date_str, state["cycle_id"])
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
        "--auto-commit", action="store_true", dest="auto_commit",
        help="VERIFY成功時に自動commitする（デフォルトOFF）",
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
