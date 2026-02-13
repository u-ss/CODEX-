# -*- coding: utf-8 -*-
"""
Research tool trace helpers.

`TOOL_CALL` / `TOOL_RESULT` を必ず対で記録するための薄いラッパー。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .context import ResearchRunContext


def _summarize(value: Any) -> Any:
    if value is None:
        return {"type": "none"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, (int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, str):
        return {
            "type": "str",
            "length": len(value),
            "preview": value[:160],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
        }
    if isinstance(value, dict):
        keys = list(value.keys())
        return {
            "type": "dict",
            "size": len(value),
            "keys": keys[:8],
        }
    return {"type": type(value).__name__}


def call_tool(
    context: ResearchRunContext,
    *,
    tool_name: str,
    call: Callable[[], Any],
    args: Optional[dict[str, Any]] = None,
    result_summary: Optional[Callable[[Any], Any]] = None,
) -> Any:
    """
    Run a tool-like callable and emit TOOL_CALL / TOOL_RESULT if logger is available.
    """
    logger = context.workflow_logger
    call_id = ""
    started = time.perf_counter()
    if logger is not None:
        call_id = logger.log_tool_call(tool_name, args=args or {})

    try:
        result = call()
        if logger is not None and call_id:
            summary = result_summary(result) if result_summary else _summarize(result)
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.log_tool_result(
                call_id=call_id,
                status="ok",
                result=summary,
                duration_ms=duration_ms,
            )
        return result
    except Exception as exc:
        if logger is not None and call_id:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.log_tool_result(
                call_id=call_id,
                status="error",
                error=str(exc),
                duration_ms=duration_ms,
            )
        raise

