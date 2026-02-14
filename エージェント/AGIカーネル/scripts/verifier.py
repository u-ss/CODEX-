#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — Verifier モジュール (v0.6.1)

タスク種別に応じた検証コマンドを実行する。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("agi_kernel")


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
            cmd = [sys.executable, "-m", "pytest", target_nodeid, "-q", "--tb=short", "--color=no"]
        elif source == "pytest" and target_path:
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
            output = (result.stdout + result.stderr)[-2000:]
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
