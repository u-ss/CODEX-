# -*- coding: utf-8 -*-
"""
Codex Exec ラッパースクリプト

問題点:
  1. stdout/stderrの混合による出力重複
  2. ターミナル幅依存のフォーマット崩れ
  3. 実行時間の可視性不足

このラッパーはこれらを解決する:
  - stdout/stderrを完全分離してキャプチャ
  - 出力の重複検出・除去
  - ファイル出力（ターミナル幅非依存）
  - 実行時間・トークン数の自動計測
  - JSON形式でのメタデータエクスポート

使用例:
  python tools/codex_exec_wrapper.py "1+1は?"
  python tools/codex_exec_wrapper.py "リポジトリ分析" -o result.md
  python tools/codex_exec_wrapper.py "分析" -o result.md --json meta.json
  python tools/codex_exec_wrapper.py "分析" --scope ".agent/workflows"
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def find_codex_command() -> str:
    """codex コマンドのパスを返す"""
    import shutil
    codex = shutil.which("codex")
    if not codex:
        print("[error] codex コマンドが見つかりません", file=sys.stderr)
        sys.exit(1)
    return codex


def _escape_shell_arg(arg: str) -> str:
    """シェル引数のエスケープ（Windows用）"""
    # ダブルクォートでくくり、内部のダブルクォートをエスケープ
    escaped = arg.replace('"', '\\"')
    return f'"{escaped}"'


def run_codex_exec(
    prompt: str,
    *,
    timeout: int = 600,
    cwd: str | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """codex exec を実行して結果を辞書で返す

    stdout/stderrを完全に分離してキャプチャする。
    Windows環境ではcodexがcodex.ps1であるため、shell=Trueで実行する。
    """
    # コマンドを文字列として構築（shell=True用）
    parts = ["codex", "exec"]
    if extra_args:
        parts.extend(extra_args)
    parts.append(_escape_shell_arg(prompt))
    cmd_str = " ".join(parts)

    work_dir = cwd or os.getcwd()

    print(f"[info] 実行開始: codex exec (timeout={timeout}s)", file=sys.stderr)
    print(f"[info] 作業ディレクトリ: {work_dir}", file=sys.stderr)
    start = time.monotonic()

    try:
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
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - start
        print(f"[error] タイムアウト ({timeout}秒超過)", file=sys.stderr)
        return {
            "stdout": e.stdout or "" if hasattr(e, "stdout") else "",
            "stderr": e.stderr or "" if hasattr(e, "stderr") else "",
            "exit_code": 124,
            "elapsed_seconds": round(elapsed, 2),
            "timed_out": True,
        }

    elapsed = time.monotonic() - start
    print(f"[info] 実行完了: {elapsed:.1f}秒, exit_code={proc.returncode}", file=sys.stderr)

    return {
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 2),
        "timed_out": False,
    }


def detect_duplicates(text: str, min_block_len: int = 80) -> list[dict]:
    """テキスト内で重複するブロックを検出する"""
    duplicates = []
    lines = text.split("\n")
    n = len(lines)

    for i in range(n):
        for j in range(i + 1, n):
            match_len = 0
            k = 0
            while i + k < j and j + k < n and lines[i + k] == lines[j + k]:
                match_len += len(lines[i + k])
                k += 1
            if match_len >= min_block_len and k >= 3:
                block = "\n".join(lines[i : i + k])
                block_hash = hashlib.sha256(block.encode("utf-8")).hexdigest()[:16]
                duplicates.append({
                    "start_line_a": i,
                    "start_line_b": j,
                    "num_lines": k,
                    "char_count": match_len,
                    "hash": block_hash,
                })
                break
        if duplicates:
            break

    return duplicates


def remove_duplicates(text: str, min_block_len: int = 80) -> str:
    """テキスト内の重複ブロックを除去する

    最初の出現を保持し、2回目以降を除去する。
    """
    lines = text.split("\n")
    n = len(lines)
    remove_ranges = []

    for i in range(n):
        for j in range(i + 1, n):
            match_len = 0
            k = 0
            while i + k < j and j + k < n and lines[i + k] == lines[j + k]:
                match_len += len(lines[i + k])
                k += 1
            if match_len >= min_block_len and k >= 3:
                # 2番目の出現（j〜j+k-1）を除去対象にする
                remove_ranges.append((j, j + k))
                break
        if remove_ranges:
            break

    if not remove_ranges:
        return text

    # 除去対象行を削除
    keep = []
    remove_set = set()
    for start, end in remove_ranges:
        for idx in range(start, end):
            remove_set.add(idx)

    for idx, line in enumerate(lines):
        if idx not in remove_set:
            keep.append(line)

    cleaned = "\n".join(keep)
    print(f"[info] 重複除去: {len(remove_set)}行を除去", file=sys.stderr)
    return cleaned


def extract_tokens_used(text: str) -> int | None:
    """'tokens used' の値を抽出"""
    match = re.search(r"tokens?\s*used\s*[\n\r]*\s*([\d,]+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def clean_output(stdout: str, stderr: str) -> str:
    """stdout/stderrから最終的なクリーンな出力を生成

    1. メインコンテンツはstdoutから取得
    2. stdoutが空ならstderrを使用
    3. 重複があれば除去
    4. 'tokens used' 行をメタデータとして分離
    """
    # メインコンテンツの決定
    content = stdout.strip()
    if not content:
        content = stderr.strip()

    if not content:
        return ""

    # 重複除去
    dups = detect_duplicates(content)
    if dups:
        content = remove_duplicates(content)

    # 'tokens used' 行を末尾から除去（メタデータなのでコンテンツに含めない）
    lines = content.split("\n")
    cleaned_lines = []
    for line in lines:
        if re.match(r"^\s*tokens?\s*used\s*$", line, re.IGNORECASE):
            continue
        if re.match(r"^\s*[\d,]+\s*$", line) and cleaned_lines and \
           re.match(r"^\s*tokens?\s*used", cleaned_lines[-1] if cleaned_lines else "", re.IGNORECASE):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def main():
    parser = argparse.ArgumentParser(
        description="Codex Exec ラッパー — 出力品質を改善して実行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", help="Codex CLIに送るプロンプト")
    parser.add_argument(
        "-o", "--output",
        help="出力ファイルパス（指定時はターミナル幅に依存しない）",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        help="メタデータのJSON出力先",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="タイムアウト秒数（デフォルト: 600秒）",
    )
    parser.add_argument(
        "--scope",
        help="分析対象のディレクトリパス（プロンプトに追加指示）",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="重複除去を無効化",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="クリーニングせずに生の出力を返す",
    )
    parser.add_argument(
        "--cwd",
        help="作業ディレクトリ（デフォルト: カレント）",
    )

    args = parser.parse_args()

    # プロンプトにスコープ指示を追加
    prompt = args.prompt
    if args.scope:
        prompt = f"{prompt}\n\n対象ディレクトリ: {args.scope} 配下のみを分析してください。"

    # codex コマンドの確認
    find_codex_command()

    # 実行
    result = run_codex_exec(
        prompt,
        timeout=args.timeout,
        cwd=args.cwd,
    )

    # メタデータ収集
    tokens_from_stdout = extract_tokens_used(result["stdout"])
    tokens_from_stderr = extract_tokens_used(result["stderr"])
    tokens_used = tokens_from_stdout or tokens_from_stderr

    # 出力のクリーニング
    if args.raw:
        final_output = result["stdout"]
    else:
        final_output = clean_output(result["stdout"], result["stderr"])

    # 重複検出レポート
    stdout_dups = detect_duplicates(result["stdout"])
    stderr_dups = detect_duplicates(result["stderr"])

    # メタデータ
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
        "exit_code": result["exit_code"],
        "elapsed_seconds": result["elapsed_seconds"],
        "timed_out": result["timed_out"],
        "tokens_used": tokens_used,
        "tokens_source": (
            "stdout" if tokens_from_stdout
            else "stderr" if tokens_from_stderr
            else "not_found"
        ),
        "stdout_chars": len(result["stdout"]),
        "stderr_chars": len(result["stderr"]),
        "output_chars": len(final_output),
        "duplicates_in_stdout": len(stdout_dups),
        "duplicates_in_stderr": len(stderr_dups),
        "duplicates_removed": len(stdout_dups) > 0 and not args.no_dedup and not args.raw,
    }

    # 出力
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_output, encoding="utf-8")
        print(f"[ok] 出力保存: {output_path}", file=sys.stderr)
        print(f"[ok] 文字数: {len(final_output)}", file=sys.stderr)
    else:
        # ターミナル出力
        print(final_output)

    # JSON メタデータ出力
    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[ok] メタデータ保存: {json_path}", file=sys.stderr)

    # サマリー表示
    print(f"\n--- 実行サマリー ---", file=sys.stderr)
    print(f"  実行時間: {result['elapsed_seconds']}秒", file=sys.stderr)
    print(f"  終了コード: {result['exit_code']}", file=sys.stderr)
    if tokens_used:
        print(f"  トークン使用量: {tokens_used:,}", file=sys.stderr)
    print(f"  stdout: {len(result['stdout']):,}文字", file=sys.stderr)
    print(f"  stderr: {len(result['stderr']):,}文字", file=sys.stderr)
    print(f"  最終出力: {len(final_output):,}文字", file=sys.stderr)
    if stdout_dups:
        print(f"  ⚠ stdout内重複: {len(stdout_dups)}ブロック検出{'（除去済）' if not args.no_dedup else '（未除去）'}", file=sys.stderr)
    if stderr_dups:
        print(f"  ⚠ stderr内重複: {len(stderr_dups)}ブロック検出", file=sys.stderr)

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
