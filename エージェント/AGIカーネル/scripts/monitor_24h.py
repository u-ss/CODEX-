#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel 24時間運用監視スクリプト

使い方:
  python scripts/monitor_24h.py --webhook-url "https://discord.com/api/webhooks/..."
  python scripts/monitor_24h.py  # Webhookなし（ログのみ）

機能:
  - --loop --interval 300 --log-json で AGI Kernel を起動
  - stdout/stderr をタイムスタンプ付きログファイルに保存
  - 異常終了時にリトライ（最大5回連続失敗で停止）
  - 24時間後に自動停止
"""

import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

# 設定
MAX_CONSECUTIVE_FAILURES = 5
MONITORING_HOURS = 24
RETRY_DELAY_SECONDS = 30

_SCRIPT_DIR = Path(__file__).resolve().parent
_KERNEL_SCRIPT = _SCRIPT_DIR / "agi_kernel.py"
_LOG_DIR = _SCRIPT_DIR.parent / "_outputs" / "monitor_logs"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AGI Kernel 24時間監視")
    parser.add_argument("--webhook-url", default=None, help="Webhook通知先URL")
    parser.add_argument("--interval", type=int, default=300, help="サイクル間隔（秒）")
    parser.add_argument("--hours", type=int, default=MONITORING_HOURS, help="監視時間（時間）")
    parser.add_argument("--lint-severity", default="error", help="Lint取込レベル")
    args = parser.parse_args()

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now(tz=JST)
    end_time = start_time + timedelta(hours=args.hours)
    log_file = _LOG_DIR / f"monitor_{start_time.strftime('%Y%m%d_%H%M%S')}.log"

    print(f"[MONITOR] 開始: {start_time.isoformat()}")
    print(f"[MONITOR] 終了予定: {end_time.isoformat()}")
    print(f"[MONITOR] ログ: {log_file}")

    # カーネル起動コマンド構築
    cmd = [
        sys.executable, str(_KERNEL_SCRIPT),
        "--loop",
        "--interval", str(args.interval),
        "--log-json",
        "--lint-severity", args.lint_severity,
    ]
    if args.webhook_url:
        cmd.extend(["--webhook-url", args.webhook_url])

    consecutive_failures = 0

    with open(log_file, "a", encoding="utf-8") as log_fh:
        while datetime.now(tz=JST) < end_time:
            ts = datetime.now(tz=JST).isoformat()
            log_fh.write(f"\n{'='*60}\n[{ts}] カーネル起動\n{'='*60}\n")
            log_fh.flush()

            try:
                result = subprocess.run(
                    cmd,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    timeout=args.hours * 3600,  # タイムアウト = 監視時間
                )
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                log_fh.write(f"[{datetime.now(tz=JST).isoformat()}] タイムアウト停止\n")
                break
            except KeyboardInterrupt:
                log_fh.write(f"[{datetime.now(tz=JST).isoformat()}] Ctrl+C 停止\n")
                print("\n[MONITOR] Ctrl+C で停止")
                break

            ts_end = datetime.now(tz=JST).isoformat()
            log_fh.write(f"[{ts_end}] 終了 exit_code={exit_code}\n")
            log_fh.flush()

            if exit_code != 0:
                consecutive_failures += 1
                print(f"[MONITOR] ⚠️ 異常終了 (exit={exit_code}, 連続失敗={consecutive_failures})")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    msg = f"[MONITOR] ❌ 連続{MAX_CONSECUTIVE_FAILURES}回失敗 → 監視停止"
                    print(msg)
                    log_fh.write(f"[{ts_end}] {msg}\n")
                    break
                print(f"[MONITOR] {RETRY_DELAY_SECONDS}秒後にリトライ...")
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                consecutive_failures = 0
                # --loop モードは正常終了 = Ctrl+C → 監視終了
                print("[MONITOR] カーネル正常終了")
                break

    duration = datetime.now(tz=JST) - start_time
    print(f"[MONITOR] 監視完了 (稼働時間: {duration})")
    print(f"[MONITOR] ログ: {log_file}")


if __name__ == "__main__":
    main()
