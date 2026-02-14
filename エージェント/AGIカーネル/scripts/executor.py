#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — Executor モジュール (v0.6.1)

Gemini SDK 統合、パッチ生成・適用・検証、プリフライト / バックアップ / ロールバック。
P1: 旧SDK (google.generativeai) フォールバック削除。google-genai 一本化。
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agi_kernel")


# ── EXECUTE/VERIFY 安全制限 ──
MAX_PATCH_FILES = 5
MAX_DIFF_LINES = 500
MAX_LLM_RETRIES = 3
LLM_RETRY_BASE_DELAY_SECONDS = 2.0
LLM_RETRY_MAX_DELAY_SECONDS = 30.0
LLM_RETRY_JITTER_SECONDS = 1.0

_COST_PER_1M = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro":   {"input": 1.25, "output": 5.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}

COMMAND_ALLOWLIST = [
    [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no"],
    [sys.executable, "tools/workflow_lint.py"],
]


# ============================================================
# Gemini SDK (google-genai 一本化 — v0.6.1 P1)
# ============================================================

class GeminiClient:
    """google-genai SDK ラッパー。v0.6.1 で旧SDK互換を完全削除。"""

    def __init__(self, backend_or_client: Any = None, client: Any = None):
        # 後方互換: GeminiClient("google-genai", client) または GeminiClient(client)
        if client is not None:
            # 2引数形式（旧 _GeminiClientCompat 互換）
            self.backend = str(backend_or_client)
            self.client = client
        else:
            # 1引数形式（新API）
            self.client = backend_or_client
            self.backend = "google-genai"

    def generate_content(self, model_name: str, prompt: str) -> Any:
        """指定モデルでコンテンツを生成する。"""
        return self.client.models.generate_content(model=model_name, contents=prompt)


def get_genai_client() -> GeminiClient:
    """Gemini SDK (google-genai) を遅延importする。

    v0.6.1: 旧SDK (google.generativeai) フォールバック削除。
    google-genai 未インストール時は明確なエラーメッセージを表示。
    """
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("環境変数 GOOGLE_API_KEY / GEMINI_API_KEY が未設定です")

    try:
        from google import genai as genai_sdk  # type: ignore
        client = genai_sdk.Client(api_key=api_key)
        return GeminiClient(client)
    except ImportError:
        raise RuntimeError(
            "google-genai がインストールされていません。\n"
            "  pip install google-genai\n"
            "※ v0.6.1 で旧SDK (google-generativeai) のサポートは終了しました。"
        )


def log_token_usage(response: Any, model_name: str, state: Optional[dict] = None) -> dict[str, Any]:
    """トークン消費をログ出力し、stateに累積する。コスト推定も計算。"""
    usage: dict[str, Any] = {"prompt": 0, "output": 0, "total": 0, "estimated_cost_usd": 0.0}
    try:
        meta = response.usage_metadata
        usage["prompt"] = getattr(meta, "prompt_token_count", 0) or 0
        usage["output"] = getattr(meta, "candidates_token_count", 0) or 0
        usage["total"] = getattr(meta, "total_token_count", 0) or 0
        cost_rates = _COST_PER_1M.get(model_name, {"input": 0.15, "output": 0.60})
        usage["estimated_cost_usd"] = round(
            usage["prompt"] * cost_rates["input"] / 1_000_000
            + usage["output"] * cost_rates["output"] / 1_000_000, 6
        )
        logger.info(f"[TOKEN] model={model_name} input={usage['prompt']} output={usage['output']} total={usage['total']} cost=${usage['estimated_cost_usd']}")
    except (AttributeError, TypeError):
        logger.info("[TOKEN] トークン情報取得不可")

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
    logger.info(f"[EXECUTE] リトライ待機 {delay:.2f}s (model={model_name})")
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
    {target_path_section}

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
    {target_path_constraint}
""")


class GeminiExecutor(Executor):
    """Gemini API でパッチを生成する実装。flash→proフォールバック付き。"""

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        strong_model_name: str = "gemini-2.5-pro",
        state: Optional[dict] = None,
    ):
        self.model_name = (
            os.environ.get("AGI_KERNEL_LLM_MODEL") or model_name
        )
        self.strong_model_name = (
            os.environ.get("AGI_KERNEL_LLM_STRONG_MODEL") or strong_model_name
        )
        self._state = state  # token_usage累積用

    def generate_patch(self, task: dict, context: str, workspace: Path) -> dict:
        """Gemini API でパッチを生成し、バリデーション済dictを返す。"""
        client = get_genai_client()
        logger.info(f"[EXECUTE] Gemini SDK backend={client.backend}")

        # target_path制約をプロンプトに注入
        target_path = task.get("target_path", "")
        if target_path:
            target_path_section = f"対象ファイル: {target_path}"
            target_path_constraint = f"- ⚠️ 変更対象は {target_path} のみ。他のファイルの変更は禁止。"
        else:
            target_path_section = ""
            target_path_constraint = ""

        prompt = _PATCH_PROMPT_TEMPLATE.format(
            title=task.get("title", ""),
            description=task.get("description", ""),
            source=task.get("source", ""),
            context=context[:3000],
            max_files=MAX_PATCH_FILES,
            target_path_section=target_path_section,
            target_path_constraint=target_path_constraint,
        )

        # フェーズ1: 通常モデルで試行
        last_error = ""
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = client.generate_content(self.model_name, prompt)
                raw = _extract_response_text(response)
                log_token_usage(response, self.model_name, self._state)
                patch = parse_patch_json(raw)
                validate_patch_result(patch, workspace)
                return patch
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = str(e)
                logger.warning(f"[EXECUTE] LLMバリデーション失敗 (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.model_name)
                continue
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[EXECUTE] LLM呼び出しエラー (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.model_name)
                continue

        # フェーズ2: 強力モデルでフォールバック
        if self.strong_model_name != self.model_name:
            logger.info(f"[EXECUTE] → 強力モデル({self.strong_model_name})でフォールバック...")
            for attempt in range(MAX_LLM_RETRIES):
                try:
                    response = client.generate_content(self.strong_model_name, prompt)
                    raw = _extract_response_text(response)
                    log_token_usage(response, self.strong_model_name, self._state)
                    patch = parse_patch_json(raw)
                    validate_patch_result(patch, workspace)
                    return patch
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    last_error = str(e)
                    logger.warning(f"[EXECUTE] 強力モデルバリデーション失敗 (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                    _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.strong_model_name)
                    continue
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"[EXECUTE] 強力モデルエラー (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                    _sleep_before_retry(attempt, MAX_LLM_RETRIES, self.strong_model_name)
                    continue

        raise RuntimeError(f"LLMパッチ生成に失敗: {last_error}")


# ============================================================
# LLM出力パーサー + バリデーション
# ============================================================

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


def parse_patch_json(raw: str) -> dict:
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


def validate_patch_result(patch: dict, workspace: Path, *, target_path: str = "") -> None:
    """パッチ結果をバリデーションする。失敗時はValueError。

    target_path が指定された場合、パッチの全ファイルがそのパスに
    限定されているかも検証する。
    """
    if not isinstance(patch, dict):
        raise ValueError("パッチ結果がdictではありません")
    files = patch.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError("files が空またはリストではありません")
    if len(files) > MAX_PATCH_FILES:
        raise ValueError(f"変更ファイル数 {len(files)} > 上限 {MAX_PATCH_FILES}")

    # target_path正規化（絶対パス→相対パス変換を試みる）
    normalized_target = ""
    if target_path:
        tp = Path(target_path)
        try:
            normalized_target = str(tp.resolve().relative_to(workspace.resolve())).replace("\\", "/")
        except ValueError:
            # 既に相対パスの場合はそのまま使用
            normalized_target = target_path.replace("\\", "/")

    ws_resolved = workspace.resolve()
    for f in files:
        if not isinstance(f, dict):
            raise ValueError("files の各要素はdictである必要があります")
        path_str = f.get("path", "")
        if not path_str:
            raise ValueError("path が空です")
        path_parts = Path(path_str).parts
        if ".." in path_parts:
            raise ValueError(f"path に '..' が含まれています: {path_str}")
        resolved = (workspace / path_str).resolve()
        try:
            resolved.relative_to(ws_resolved)
        except ValueError:
            raise ValueError(f"path がワークスペース外です: {path_str}")
        action = f.get("action", "")
        if action not in ("create", "modify"):
            raise ValueError(f"action が不正です: {action}")
        if "content" not in f:
            raise ValueError(f"content がありません: {path_str}")

        # target_path制約: 指定されたファイルのみ変更可能
        if normalized_target:
            patch_path_normalized = path_str.replace("\\", "/")
            if patch_path_normalized != normalized_target:
                raise ValueError(
                    f"target_path制約違反: {path_str} は対象外 (許可: {normalized_target})"
                )


# ============================================================
# パッチ適用 / Preflight / Backup / Rollback / Diff
# ============================================================

def apply_patch(patch: dict, workspace: Path) -> list[Path]:
    """パッチをファイルシステムに適用する。変更したパスのリストを返す。"""
    modified_paths: list[Path] = []
    for f in patch["files"]:
        target = workspace / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8")
        modified_paths.append(target)
    return modified_paths


def preflight_check(workspace: Path) -> dict:
    """EXECUTE前の安全チェック。

    Returns:
        {"ok": bool, "reason": str, "git_available": bool}
    """
    try:
        v = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if v.returncode != 0:
            return {"ok": True, "reason": "", "git_available": False}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"ok": True, "reason": "", "git_available": False}

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


def backup_targets(
    patch: dict, workspace: Path, backup_dir: Path,
) -> dict[str, Optional[Path]]:
    """パッチ適用前に変更対象ファイルのバックアップを作成する。"""
    backup_map: dict[str, Optional[Path]] = {}
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in patch["files"]:
        rel = f["path"]
        original = workspace / rel
        if original.exists():
            bak_path = backup_dir / rel
            bak_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(original), str(bak_path))
            backup_map[rel] = bak_path
        else:
            backup_map[rel] = None
    return backup_map


def rollback_with_backup(
    modified_paths: list[Path],
    backup_map: dict[str, Optional[Path]],
    workspace: Path,
) -> None:
    """バックアップから復元する。新規ファイルは削除。"""
    for p in modified_paths:
        try:
            rel = str(p.relative_to(workspace)).replace("\\", "/")
        except ValueError:
            continue
        bak = backup_map.get(rel)
        if bak is not None:
            try:
                shutil.copy2(str(bak), str(p))
            except OSError:
                try:
                    subprocess.run(
                        ["git", "checkout", "--", rel],
                        cwd=str(workspace),
                        capture_output=True, text=True, timeout=10,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass
        else:
            if p.exists():
                p.unlink()


def restore_rollback_context(
    state: dict, workspace: Path, output_dir: Path,
) -> tuple[list[Path], dict[str, Optional[Path]]]:
    """ステートからmodified_pathsとbackup_mapを復元する（RESUME安全性）。"""
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
                backup_map[rel] = None

    return modified_paths, backup_map


def compute_patch_diff_lines(
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
            old_lines = bak.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        else:
            old_lines = []

        diff = list(difflib.unified_diff(old_lines, new_lines, n=0))
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                total += 1
            elif line.startswith("-") and not line.startswith("---"):
                total += 1
    return total


def build_execute_context(task: dict, scan_results: dict,
                          workspace: Path | None = None) -> str:
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

    target_path = task.get("target_path", "")
    if target_path and workspace:
        target_file = workspace / target_path
        if target_file.exists() and target_file.is_file():
            try:
                content = target_file.read_text(encoding="utf-8", errors="replace")
                if len(content) > 4000:
                    content = content[:4000] + "\n... (截端)"
                parts.append(f"\n--- target_path: {target_path} ---")
                parts.append(content)
            except OSError:
                pass

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
                break

    if workspace:
        try:
            tree_lines = []
            for p in sorted(workspace.rglob("*")):
                rel = p.relative_to(workspace)
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
