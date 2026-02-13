#!/usr/bin/env python3
"""
examples/log_demo.py — ログ基盤デモ

実行:
  python examples/log_demo.py

出力:
  _logs/demo.jsonl（ローテ付き）
"""
from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.logger import setup_logger, info, warn, error  # noqa: E402


def main() -> None:
    # 小さいmax_bytesでローテーションを確認
    log_file = Path("_logs") / "demo.jsonl"
    setup_logger(path=log_file, max_bytes=2048, max_files=3)

    def safe_print(msg: str) -> None:
        try:
            print(msg)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))

    safe_print(f"ログ出力先: {log_file}")
    safe_print("ローテーション: max_bytes=2048, max_files=3")
    safe_print("")

    # 通常ログ
    info("app_start", version="1.0.0", env="development")
    info("config_loaded", database="sqlite", cache="memory")

    # ユーザー操作
    for i in range(10):
        info("user_action", user=f"user_{i}", action="click", page=f"/page/{i}")

    # 秘密情報を含むログ（redactされる）
    info("auth_attempt", username="taro", password="super_secret_123")
    info("api_request", url="/api/v1/data", token="Bearer eyJhbGciOiJIUzI1NiJ9")
    info("config_detail", settings={"db_password": "pg_pass", "host": "localhost", "port": 5432})
    info("webhook", headers={"Authorization": "Basic dXNlcjpwYXNz", "Content-Type": "application/json"})

    # 警告
    warn("slow_query", duration_ms=3500, query="SELECT * FROM large_table")
    warn("disk_usage_high", usage_percent=92.5, path="/data")

    # エラー
    try:
        result = 1 / 0
    except ZeroDivisionError as e:
        error("division_error", err=e, operation="calculate_ratio")

    try:
        raise ConnectionError("database connection timeout")
    except ConnectionError as e:
        error("db_connection_failed", err=e, retry_count=3, api_key="sk-1234567890")

    # 大量ログでローテーションを発生させる
    for i in range(50):
        info(f"bulk_event_{i}", data="x" * 30, seq=i, token="should_be_masked")

    # 結果表示
    safe_print("--- 生成されたファイル ---")
    log_dir = Path("_logs")
    for f in sorted(log_dir.glob("demo.jsonl*")):
        size = f.stat().st_size
        safe_print(f"  {f.name}: {size} bytes")

    # 全行JSONパース検証
    safe_print("")
    safe_print("--- JSON検証 ---")
    import json
    total_lines = 0
    for f in sorted(log_dir.glob("demo.jsonl*")):
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue
        lines = content.splitlines()
        for line in lines:
            json.loads(line)  # パース失敗なら例外
        total_lines += len(lines)
        safe_print(f"  {f.name}: {len(lines)}行 OK")

    safe_print(f"\n合計 {total_lines} 行、全行JSON OK")

    # redact検証
    safe_print("")
    safe_print("--- Redact検証 ---")
    all_content = ""
    for f in sorted(log_dir.glob("demo.jsonl*")):
        all_content += f.read_text(encoding="utf-8")

    secrets = ["super_secret_123", "eyJhbGciOiJIUzI1NiJ9", "pg_pass", "dXNlcjpwYXNz", "sk-1234567890"]
    leaked = [s for s in secrets if s in all_content]
    if leaked:
        safe_print(f"  NG: 平文漏れ: {leaked}")
    else:
        safe_print("  平文の秘密情報なし OK")


if __name__ == "__main__":
    main()
