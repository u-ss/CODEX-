# -*- coding: utf-8 -*-
"""
Desktop Tools - ツールモジュール

/desktop ワークフローの定型作業をモジュール化したツール群。
"""

from .chatgpt import (
    ask_chatgpt,
    ask_chatgpt_sync,
    connect_to_chatgpt,
    check_session_valid,
    detect_rate_limit,
    wait_for_generation_complete,
    get_latest_response,
    ChatGPTError,
    SessionExpiredError,
    RateLimitError,
    PageNotFoundError,
    SELECTORS as CHATGPT_SELECTORS,
)

from .screenshot import (
    capture_all_monitors,
    capture_monitor,
    get_monitor_count,
    DEFAULT_SS_DIR,
)

__all__ = [
    # ChatGPT
    "ask_chatgpt",
    "ask_chatgpt_sync",
    "connect_to_chatgpt",
    "check_session_valid",
    "detect_rate_limit",
    "wait_for_generation_complete",
    "get_latest_response",
    "ChatGPTError",
    "SessionExpiredError",
    "RateLimitError",
    "PageNotFoundError",
    "CHATGPT_SELECTORS",
    # Screenshot
    "capture_all_monitors",
    "capture_monitor",
    "get_monitor_count",
    "DEFAULT_SS_DIR",
]
