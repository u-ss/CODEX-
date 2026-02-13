# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - FailureEvent Module
失敗分類（Transient/Deterministic/Unsafe）+ reason_code
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Literal
import time
import uuid
import re


class FailureKind(str, Enum):
    """失敗種別"""
    TRANSIENT = "transient"       # リトライ価値あり（timeout, network）
    DETERMINISTIC = "deterministic"  # 同条件で再発（not_found, permission）
    UNSAFE = "unsafe"             # 危険操作検出（即停止）


class ReasonCode(str, Enum):
    """失敗理由コード"""
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    NOT_VISIBLE = "not_visible"
    NOT_ENABLED = "not_enabled"
    STALE = "stale"
    NAVIGATION = "navigation"
    DISCONNECTED = "disconnected"
    MODAL_BLOCKED = "modal_blocked"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    FOCUS_LOST = "focus_lost"
    UNKNOWN = "unknown"


LayerName = Literal["cdp", "uia", "pixel", "vlm"]


@dataclass(frozen=True)
class FailureEvidence:
    """失敗証拠"""
    url: Optional[str] = None
    window_title: Optional[str] = None
    dom_fingerprint: Optional[str] = None
    uia_fingerprint: Optional[str] = None
    roi_hash: Optional[str] = None
    note: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureEvent:
    """失敗イベント"""
    event_id: str
    ts_ms: int
    trace_id: str
    step_id: int
    app_id: str
    screen_family: str
    screen_key: Optional[str]
    layer: LayerName
    action_type: str
    locator_id: Optional[str]
    kind: FailureKind
    reason: ReasonCode
    message: str
    retryable: bool
    evidence: FailureEvidence


def create_failure_event(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str],
    layer: LayerName,
    action_type: str,
    locator_id: Optional[str],
    kind: FailureKind,
    reason: ReasonCode,
    message: str,
    retryable: bool,
    evidence: Optional[FailureEvidence] = None,
) -> FailureEvent:
    """FailureEventを作成"""
    return FailureEvent(
        event_id=str(uuid.uuid4()),
        ts_ms=int(time.time() * 1000),
        trace_id=trace_id,
        step_id=step_id,
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        layer=layer,
        action_type=action_type,
        locator_id=locator_id,
        kind=kind,
        reason=reason,
        message=message[:500],
        retryable=retryable,
        evidence=evidence or FailureEvidence(),
    )


# 失敗パターン（分類用）
UNSAFE_KEYWORDS = (
    "delete", "remove", "permanently", "pay", "buy", "purchase", 
    "grant", "allow", "uninstall", "format",
    "削除", "完全に", "永久", "購入", "支払い", "許可", "アンインストール",
)

TRANSIENT_PATTERNS = [
    r"timeout",
    r"connection refused",
    r"network error",
    r"ECONNRESET",
    r"rate limit",
    r"loading",
]

DETERMINISTIC_PATTERNS = [
    r"not found",
    r"element not found",
    r"no such element",
    r"permission denied",
    r"access denied",
]


@dataclass(frozen=True)
class ClassifyInput:
    """分類入力"""
    exception_name: str
    exception_message: str
    layer: LayerName
    action_type: str
    saw_modal: Optional[bool] = None
    modal_text: Optional[str] = None
    dom_not_found: Optional[bool] = None
    uia_not_found: Optional[bool] = None


def classify_failure(inp: ClassifyInput) -> tuple[FailureKind, ReasonCode, bool, str]:
    """
    失敗を分類
    
    Returns:
        (kind, reason, retryable, short_message)
    """
    msg_lower = inp.exception_message.lower()
    
    # Unsafe検出（最優先）
    if inp.modal_text:
        modal_lower = inp.modal_text.lower()
        for kw in UNSAFE_KEYWORDS:
            if kw in modal_lower:
                return (
                    FailureKind.UNSAFE,
                    ReasonCode.MODAL_BLOCKED,
                    False,
                    f"危険モーダル検出: {kw}"
                )
    
    # Transientパターン
    for pattern in TRANSIENT_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            reason = ReasonCode.TIMEOUT if "timeout" in msg_lower else ReasonCode.UNKNOWN
            return (FailureKind.TRANSIENT, reason, True, "一時的エラー、リトライ可能")
    
    # Deterministicパターン
    for pattern in DETERMINISTIC_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            reason = ReasonCode.NOT_FOUND if "not found" in msg_lower else ReasonCode.PERMISSION
            return (FailureKind.DETERMINISTIC, reason, False, "恒久的エラー、修正が必要")
    
    # DOM/UIA not found
    if inp.dom_not_found or inp.uia_not_found:
        return (FailureKind.DETERMINISTIC, ReasonCode.NOT_FOUND, False, "要素が見つからない")
    
    # Modal blocked
    if inp.saw_modal:
        return (FailureKind.DETERMINISTIC, ReasonCode.MODAL_BLOCKED, False, "モーダルでブロック")
    
    # Unknown
    return (FailureKind.TRANSIENT, ReasonCode.UNKNOWN, True, "不明なエラー")


def get_failure_weight(kind: FailureKind) -> float:
    """CB用の失敗重み"""
    weights = {
        FailureKind.TRANSIENT: 0.5,
        FailureKind.DETERMINISTIC: 2.0,
        FailureKind.UNSAFE: float('inf'),
    }
    return weights.get(kind, 1.0)
