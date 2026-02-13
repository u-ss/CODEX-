# -*- coding: utf-8 -*-
"""
Codex Exec 出力品質の診断テスト

codex exec 実行時の以下の問題を特定するための7つのテスト:
  1. 出力の重複（stdout/stderr混合 or ストリーミング+最終出力の二重出力）
  2. フォーマット崩れ（ターミナル幅依存）
  3. トークン情報の出力先特定
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# テストのタイムアウト（Codex CLIは応答に時間がかかる）
CODEX_TIMEOUT = 300  # 5分

# codex コマンドの存在確認
def _codex_available() -> bool:
    """codex CLIがPATHに存在するか"""
    return shutil.which("codex") is not None

# codex exec を実行して stdout/stderr を個別に取得するヘルパー
def _escape_shell_arg(arg: str) -> str:
    """シェル引数のエスケープ（Windows用）"""
    escaped = arg.replace('"', '\\"')
    return f'"{escaped}"'


def _run_codex_exec(
    prompt: str,
    *,
    extra_args: list[str] | None = None,
    timeout: int = CODEX_TIMEOUT,
    cwd: str | None = None,
) -> dict:
    """codex exec を subprocess で実行し、結果を辞書で返す
    Windows環境ではcodexがcodex.ps1であるためshell=Trueで実行する。"""
    # コマンドを文字列として構築（shell=True用）
    parts = ["codex", "exec"]
    if extra_args:
        parts.extend(extra_args)
    parts.append(_escape_shell_arg(prompt))
    cmd_str = " ".join(parts)

    work_dir = cwd or str(Path(__file__).resolve().parent.parent)
    start = time.monotonic()
    proc = subprocess.run(
        cmd_str,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=work_dir,
        encoding="utf-8",
        errors="replace",
        shell=True,
    )
    elapsed = time.monotonic() - start

    return {
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 2),
    }


def _find_duplicated_blocks(text: str, min_block_len: int = 80) -> list[str]:
    """テキスト内で重複するブロックを検出する。
    min_block_len 文字以上の連続一致を重複とみなす。"""
    duplicates = []
    lines = text.split("\n")
    n = len(lines)
    for i in range(n):
        for j in range(i + 1, n):
            # 同一行の連続一致を探す
            match_len = 0
            k = 0
            while i + k < j and j + k < n and lines[i + k] == lines[j + k]:
                match_len += len(lines[i + k])
                k += 1
            if match_len >= min_block_len and k >= 3:
                block = "\n".join(lines[i : i + k])
                duplicates.append(block)
                break  # 最初の重複で十分
        if duplicates:
            break
    return duplicates


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _codex_available(), reason="codex CLI not installed")
class TestCodexExecOutput:
    """Codex Exec 出力品質の診断テスト群"""

    # ----------------------------------------------------------------
    # T1: stdout/stderr 分離テスト
    # ----------------------------------------------------------------
    def test_stdout_stderr_separation(self):
        """stdout と stderr に同じ内容が重複して出ないことを確認"""
        result = _run_codex_exec("1+1の答えを数字だけで答えてください")

        stdout = result["stdout"].strip()
        stderr = result["stderr"].strip()

        # 結果を記録
        print(f"=== stdout ({len(stdout)} chars) ===")
        print(stdout[:500])
        print(f"=== stderr ({len(stderr)} chars) ===")
        print(stderr[:500])
        print(f"=== exit_code: {result['exit_code']} ===")

        # stderrが空 or stdoutと異なることを確認
        if stdout and stderr:
            # 同一内容がstdoutとstderrの両方に出ていないか
            overlap_ratio = 0
            if len(stdout) > 20 and len(stderr) > 20:
                # stdoutの中にstderrの内容が含まれているか（逆も）
                shorter = stdout if len(stdout) <= len(stderr) else stderr
                longer = stderr if len(stdout) <= len(stderr) else stdout
                if shorter in longer:
                    overlap_ratio = 1.0
                else:
                    # 行単位で重複率を計算
                    stdout_lines = set(stdout.split("\n"))
                    stderr_lines = set(stderr.split("\n"))
                    if stdout_lines:
                        overlap_ratio = len(stdout_lines & stderr_lines) / len(stdout_lines)

            print(f"=== overlap_ratio: {overlap_ratio:.2%} ===")
            # 50%以上の重複は問題
            assert overlap_ratio < 0.5, (
                f"stdout/stderr間で{overlap_ratio:.0%}の重複を検出。"
                "CLIがストリーミング出力と最終出力を両方出力している可能性。"
            )

    # ----------------------------------------------------------------
    # T2: -o ファイル出力テスト
    # ----------------------------------------------------------------
    def test_file_output_no_duplication(self, tmp_path):
        """ファイル出力時に重複がないことを確認"""
        output_file = tmp_path / "codex_output.md"
        result = _run_codex_exec(
            "1+1の答えを数字だけで答えてください",
            extra_args=["-o", str(output_file)],
        )

        print(f"=== exit_code: {result['exit_code']} ===")
        print(f"=== file exists: {output_file.exists()} ===")

        if output_file.exists():
            content = output_file.read_text(encoding="utf-8")
            print(f"=== file content ({len(content)} chars) ===")
            print(content[:500])

            dups = _find_duplicated_blocks(content)
            assert len(dups) == 0, (
                f"ファイル出力に{len(dups)}個の重複ブロックを検出:\n"
                + dups[0][:200] if dups else ""
            )
        else:
            # -o オプションが使えない場合はstdoutをチェック
            print("=== -o option may not be supported, checking stdout ===")
            dups = _find_duplicated_blocks(result["stdout"])
            assert len(dups) == 0, f"stdout出力に重複を検出"

    # ----------------------------------------------------------------
    # T3: 短いプロンプトでの再現テスト
    # ----------------------------------------------------------------
    def test_short_prompt_no_duplication(self):
        """短いプロンプト（短い応答想定）で重複が発生しないことを確認"""
        result = _run_codex_exec("Hello")

        combined = result["stdout"] + "\n" + result["stderr"]
        print(f"=== combined ({len(combined)} chars) ===")
        print(combined[:500])

        dups = _find_duplicated_blocks(combined, min_block_len=30)
        assert len(dups) == 0, (
            f"短いプロンプトでも重複が発生:\n{dups[0][:200] if dups else ''}"
        )

    # ----------------------------------------------------------------
    # T4: 長いプロンプトでの再現テスト
    # ----------------------------------------------------------------
    def test_long_prompt_duplication_rate(self):
        """長いプロンプト（多い応答想定）で重複率を測定"""
        prompt = (
            "このディレクトリにあるREADME.mdの内容を要約してください。"
            "3つのポイントに分けて、各ポイントを1行で書いてください。"
        )
        result = _run_codex_exec(prompt)

        combined = result["stdout"] + "\n---STDERR---\n" + result["stderr"]
        print(f"=== combined ({len(combined)} chars) ===")
        print(combined[:1000])

        dups = _find_duplicated_blocks(combined)
        if dups:
            dup_chars = sum(len(d) for d in dups)
            total_chars = len(combined)
            dup_ratio = dup_chars / total_chars if total_chars > 0 else 0
            print(f"=== dup_ratio: {dup_ratio:.2%} ({dup_chars}/{total_chars}) ===")
            # 重複率が10%以上は問題
            assert dup_ratio < 0.10, (
                f"長いプロンプトで{dup_ratio:.0%}の重複率を検出。"
            )

    # ----------------------------------------------------------------
    # T5: 出力ハッシュ一致テスト
    # ----------------------------------------------------------------
    def test_output_hash_comparison(self):
        """重複部分のsha256ハッシュが完全一致するか検証"""
        result = _run_codex_exec(
            "Pythonでfizzbuzzを書いてください。コードだけ返してください。"
        )

        combined = result["stdout"] + "\n" + result["stderr"]
        dups = _find_duplicated_blocks(combined, min_block_len=50)

        if not dups:
            print("=== 重複なし。テスト合格 ===")
            return

        # 重複ブロックのハッシュを取る
        for i, dup in enumerate(dups):
            h = hashlib.sha256(dup.encode("utf-8")).hexdigest()[:16]
            print(f"=== dup[{i}] hash: {h} len: {len(dup)} ===")
            print(dup[:200])

        # 重複があること自体が問題
        assert len(dups) == 0, (
            f"完全一致する重複ブロックを{len(dups)}個検出。"
            "CLIが同一コンテンツを2回出力している。"
        )

    # ----------------------------------------------------------------
    # T6: フォーマット保全テスト
    # ----------------------------------------------------------------
    def test_formatting_preservation(self, tmp_path):
        """Markdown出力のフォーマットが崩れていないか検証"""
        prompt = (
            "以下のフォーマットを厳守して答えてください:\n"
            "## セクション1\n\n- ポイント1\n- ポイント2\n\n"
            "## セクション2\n\n- ポイント3\n- ポイント4\n\n"
            "テーマ: Pythonの利点"
        )
        result = _run_codex_exec(prompt)
        output = result["stdout"]

        print(f"=== stdout ({len(output)} chars) ===")
        print(output[:800])

        # 基本的なMarkdown構造チェック
        issues = []

        # 見出しの直後に改行がない（フォーマット崩れ）
        broken_headers = re.findall(r"^(#{1,4} .+\S)(\S)", output, re.MULTILINE)
        if broken_headers:
            issues.append(f"見出し直後に改行なし: {len(broken_headers)}箇所")

        # リスト項目の途中で改行が入っている
        broken_lists = re.findall(r"^- .{10,}\n\s{2,}\S", output, re.MULTILINE)
        if broken_lists:
            issues.append(f"リスト項目の途中折り返し: {len(broken_lists)}箇所")

        if issues:
            print(f"=== フォーマット問題: {issues} ===")

        # 致命的な崩れ（見出しが完全に壊れている）はエラー
        assert len(broken_headers) == 0, (
            f"Markdownフォーマットが崩壊: {issues}"
        )

    # ----------------------------------------------------------------
    # T7: tokens used の出力先特定
    # ----------------------------------------------------------------
    def test_tokens_used_location(self):
        """'tokens used' がstdoutとstderrのどちらに出力されるか特定"""
        result = _run_codex_exec("1+1は?")

        stdout_has_tokens = "tokens" in result["stdout"].lower()
        stderr_has_tokens = "tokens" in result["stderr"].lower()

        print(f"=== tokens in stdout: {stdout_has_tokens} ===")
        print(f"=== tokens in stderr: {stderr_has_tokens} ===")

        if stdout_has_tokens:
            # tokensがstdoutに含まれる → メタデータがコンテンツに混入
            # 抽出して確認
            match = re.search(r"tokens?\s*used\s*[\n\r]*\s*([\d,]+)", result["stdout"], re.IGNORECASE)
            if match:
                print(f"=== tokens value in stdout: {match.group(1)} ===")

        if stderr_has_tokens:
            match = re.search(r"tokens?\s*used\s*[\n\r]*\s*([\d,]+)", result["stderr"], re.IGNORECASE)
            if match:
                print(f"=== tokens value in stderr: {match.group(1)} ===")

        # トークン情報がstdoutに混入している場合は問題
        # （コンテンツとメタデータの分離が不十分）
        if stdout_has_tokens and not stderr_has_tokens:
            print("=== WARNING: tokens info in stdout only (content contamination) ===")


# ---------------------------------------------------------------------------
# メイン実行（pytest以外からも直接実行可能）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=600", "-s"])
