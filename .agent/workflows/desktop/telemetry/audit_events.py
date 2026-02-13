# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - Audit Events
監査ログ（Decision/Action/Verify/Recovery）
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Literal
import time
import uuid
import json
import re


EventType = Literal["observation", "decision", "action", "verify", "recovery"]


@dataclass(frozen=True)
class AuditEventBase:
    """監査イベント基底"""
    event_id: str
    ts_ms: int
    trace_id: str
    step_id: int
    event_type: EventType
    app_id: str
    screen_family: str
    screen_key: Optional[str]


@dataclass(frozen=True)
class DecisionEvent(AuditEventBase):
    """判断イベント"""
    chosen_layer: str
    chosen_locator_id: Optional[str]
    scores_json: str
    decision_source: str  # "rule" | "learned" | "fallback"
    cb_snapshot_json: str


@dataclass(frozen=True)
class ActionEvent(AuditEventBase):
    """操作イベント"""
    action_type: str
    target_fingerprint: str
    input_redacted: Optional[str]
    input_hash: Optional[str]


@dataclass(frozen=True)
class VerifyEvent(AuditEventBase):
    """検証イベント"""
    success: bool
    verify_strength: str  # "strong" | "weak"
    failure_event_id: Optional[str]
    confidence: float


@dataclass(frozen=True)
class RecoveryEvent(AuditEventBase):
    """回復イベント"""
    tier: int
    recovery_action: str
    result: str  # "success" | "fail" | "skipped"
    notes: Optional[str] = None


# ヘルパー関数

def generate_event_id() -> str:
    """イベントID生成"""
    return str(uuid.uuid4())


def now_ms() -> int:
    """現在時刻（ミリ秒）"""
    return int(time.time() * 1000)


def start_trace(*, app_id: str) -> str:
    """トレース開始"""
    return f"{app_id}-{uuid.uuid4().hex[:12]}"


class StepIdCounter:
    """ステップIDカウンター"""
    def __init__(self):
        self._counters: dict[str, int] = {}
    
    def next(self, trace_id: str) -> int:
        """次のステップID"""
        current = self._counters.get(trace_id, 0)
        self._counters[trace_id] = current + 1
        return current + 1


# グローバルカウンター
_step_counter = StepIdCounter()


def next_step_id(trace_id: str) -> int:
    """次のステップID取得"""
    return _step_counter.next(trace_id)


# リダクション

@dataclass(frozen=True)
class RedactionConfig:
    """リダクション設定"""
    keep_prefix: int = 2
    keep_suffix: int = 2
    max_len: int = 64
    mask_char: str = "*"
    redact_patterns: tuple[str, ...] = (
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\d{3}-\d{4}-\d{4}",
        r"\d{4}-\d{4}-\d{4}-\d{4}",
    )


def redact_text(text: str, cfg: RedactionConfig) -> str:
    """テキストをリダクト"""
    if not text:
        return ""
    
    result = text
    
    # パターンマッチでリダクト
    for pattern in cfg.redact_patterns:
        result = re.sub(pattern, cfg.mask_char * 8, result)
    
    # 長すぎる場合はトリミング
    if len(result) > cfg.max_len:
        result = result[:cfg.max_len] + "..."
    
    return result


def hash_text(text: str) -> str:
    """テキストハッシュ"""
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


# イベント作成ヘルパー

def create_decision_event(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str],
    chosen_layer: str,
    chosen_locator_id: Optional[str],
    scores: dict,
    decision_source: str,
    cb_snapshot: dict,
) -> DecisionEvent:
    """判断イベント作成"""
    return DecisionEvent(
        event_id=generate_event_id(),
        ts_ms=now_ms(),
        trace_id=trace_id,
        step_id=step_id,
        event_type="decision",
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        chosen_layer=chosen_layer,
        chosen_locator_id=chosen_locator_id,
        scores_json=json.dumps(scores, ensure_ascii=False),
        decision_source=decision_source,
        cb_snapshot_json=json.dumps(cb_snapshot, ensure_ascii=False),
    )


def create_action_event(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str],
    action_type: str,
    target_fingerprint: str,
    input_text: Optional[str] = None,
    redaction_cfg: Optional[RedactionConfig] = None,
) -> ActionEvent:
    """操作イベント作成"""
    cfg = redaction_cfg or RedactionConfig()
    return ActionEvent(
        event_id=generate_event_id(),
        ts_ms=now_ms(),
        trace_id=trace_id,
        step_id=step_id,
        event_type="action",
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        action_type=action_type,
        target_fingerprint=target_fingerprint,
        input_redacted=redact_text(input_text, cfg) if input_text else None,
        input_hash=hash_text(input_text) if input_text else None,
    )


def create_verify_event(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str],
    success: bool,
    verify_strength: str,
    failure_event_id: Optional[str],
    confidence: float,
) -> VerifyEvent:
    """検証イベント作成"""
    return VerifyEvent(
        event_id=generate_event_id(),
        ts_ms=now_ms(),
        trace_id=trace_id,
        step_id=step_id,
        event_type="verify",
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        success=success,
        verify_strength=verify_strength,
        failure_event_id=failure_event_id,
        confidence=confidence,
    )


def create_recovery_event(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str],
    tier: int,
    recovery_action: str,
    result: str,
    notes: Optional[str] = None,
) -> RecoveryEvent:
    """回復イベント作成"""
    return RecoveryEvent(
        event_id=generate_event_id(),
        ts_ms=now_ms(),
        trace_id=trace_id,
        step_id=step_id,
        event_type="recovery",
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        tier=tier,
        recovery_action=recovery_action,
        result=result,
        notes=notes,
    )


def event_to_dict(event: AuditEventBase) -> dict:
    """イベントを辞書変換"""
    return asdict(event)


def event_to_json(event: AuditEventBase) -> str:
    """イベントをJSON変換"""
    return json.dumps(event_to_dict(event), ensure_ascii=False)
