# recovery_strategy.py - 失敗からのリカバリ戦略
# ChatGPT 5.2相談（ラリー2,4）に基づく実装

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class FailureType(Enum):
    """失敗タイプの分類"""
    TRANSIENT = auto()      # 一時的（timeout, network_flaky, page_not_ready）
    DETERMINISTIC = auto()  # 確定的（element_not_found, stale_selector, event_mismatch）
    POLICY_GATE = auto()    # ポリシー系（login_modal_shown, age_gate, terms_required）
    ANTI_BOT = auto()       # BOT対策（CAPTCHA, 403/429）
    FATAL = auto()          # 致命的（crash, permission_denied）


class RecoveryAction(Enum):
    """リカバリアクション"""
    RETRY_SAME = auto()         # 同一候補で再試行
    SWITCH_CANDIDATE = auto()   # 別候補に切替
    SWITCH_ACTION = auto()      # 別アクション（例: Enter→ボタンクリック）
    PAGE_RELOAD = auto()        # ページ再読込
    ALTERNATE_ROUTE = auto()    # 別ルート（URLクエリ等）
    EXPLORE_MODE = auto()       # 探索モード起動
    ABORT = auto()              # 中断
    HUMAN_HANDOFF = auto()      # 人間に引き継ぎ


@dataclass
class RecoveryDecision:
    """リカバリ判断"""
    action: RecoveryAction
    reason: str
    detail: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


@dataclass
class FailureEvent:
    """失敗イベント"""
    failure_type: FailureType
    symptom: str  # login_modal_shown, element_not_found, etc.
    detail: Dict[str, Any] = None
    retry_count: int = 0
    
    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


class RecoveryStrategy:
    """
    失敗タイプに基づいてリカバリ戦略を決定。
    エスカレーション: 同一再試行 → 候補切替 → 別ルート → 探索モード → 中断
    """
    
    MAX_TRANSIENT_RETRIES = 2
    MAX_DETERMINISTIC_RETRIES = 1
    MAX_ESCALATION_LEVEL = 4
    
    def __init__(self):
        self.escalation_level = 0
        self.failure_history: List[FailureEvent] = []
    
    def reset(self) -> None:
        """リセット"""
        self.escalation_level = 0
        self.failure_history = []
    
    def decide(self, failure: FailureEvent) -> RecoveryDecision:
        """
        失敗イベントに基づいてリカバリ判断を返す。
        """
        self.failure_history.append(failure)
        ft = failure.failure_type
        symptom = failure.symptom
        retry_count = failure.retry_count
        
        # FATAL: 即座に中断
        if ft == FailureType.FATAL:
            return RecoveryDecision(
                action=RecoveryAction.ABORT,
                reason=f"致命的エラー: {symptom}",
                detail=failure.detail,
            )
        
        # ANTI_BOT: 人間に引き継ぎ（自動操作は危険）
        if ft == FailureType.ANTI_BOT:
            return RecoveryDecision(
                action=RecoveryAction.HUMAN_HANDOFF,
                reason=f"BOT対策検知: {symptom}",
                detail=failure.detail,
            )
        
        # POLICY_GATE: 即座に別ルートへ
        if ft == FailureType.POLICY_GATE:
            if symptom == "login_modal_shown":
                return RecoveryDecision(
                    action=RecoveryAction.ALTERNATE_ROUTE,
                    reason="ログインモーダル検知 → URLクエリ方式へ",
                    detail={"suggested_route": "url_query"},
                )
            return RecoveryDecision(
                action=RecoveryAction.ALTERNATE_ROUTE,
                reason=f"ポリシーゲート: {symptom}",
                detail=failure.detail,
            )
        
        # TRANSIENT: 短い待機後に再試行（最大2回）
        if ft == FailureType.TRANSIENT:
            if retry_count < self.MAX_TRANSIENT_RETRIES:
                return RecoveryDecision(
                    action=RecoveryAction.RETRY_SAME,
                    reason=f"一時的エラー: {symptom}（リトライ {retry_count + 1}/{self.MAX_TRANSIENT_RETRIES}）",
                    detail={"wait_ms": 500 * (retry_count + 1)},
                )
            # リトライ上限 → 別候補へ
            self.escalation_level += 1
            return RecoveryDecision(
                action=RecoveryAction.SWITCH_CANDIDATE,
                reason=f"一時的エラー続行 → 別候補へ",
                detail=failure.detail,
            )
        
        # DETERMINISTIC: 即座にエスカレーション
        if ft == FailureType.DETERMINISTIC:
            self.escalation_level += 1
            
            if self.escalation_level == 1:
                return RecoveryDecision(
                    action=RecoveryAction.SWITCH_CANDIDATE,
                    reason=f"確定的エラー: {symptom} → 別候補へ",
                    detail=failure.detail,
                )
            elif self.escalation_level == 2:
                return RecoveryDecision(
                    action=RecoveryAction.SWITCH_ACTION,
                    reason=f"候補切替失敗 → 別アクションへ",
                    detail={"suggestion": "Enter → button click"},
                )
            elif self.escalation_level == 3:
                return RecoveryDecision(
                    action=RecoveryAction.PAGE_RELOAD,
                    reason=f"アクション切替失敗 → ページ再読込",
                    detail=failure.detail,
                )
            elif self.escalation_level == 4:
                return RecoveryDecision(
                    action=RecoveryAction.EXPLORE_MODE,
                    reason=f"再読込失敗 → 探索モード起動",
                    detail=failure.detail,
                )
            else:
                return RecoveryDecision(
                    action=RecoveryAction.ABORT,
                    reason=f"エスカレーション上限到達",
                    detail=failure.detail,
                )
        
        # フォールバック
        return RecoveryDecision(
            action=RecoveryAction.ABORT,
            reason=f"未知の失敗タイプ: {ft}",
            detail=failure.detail,
        )
    
    def classify_symptom(self, symptom: str) -> FailureType:
        """
        症状から失敗タイプを推定。
        """
        transient_symptoms = {
            "timeout", "network_flaky", "page_not_ready", "loading",
            "request_blocked", "connection_error", "retry_after",
        }
        deterministic_symptoms = {
            "element_not_found", "stale_selector", "event_mismatch",
            "overlay_blocking", "wrong_element", "no_navigation",
        }
        policy_symptoms = {
            "login_modal_shown", "login_required", "age_gate",
            "terms_required", "cookie_consent", "region_block",
        }
        antibot_symptoms = {
            "captcha_detected", "rate_limited", "403_forbidden",
            "429_too_many", "bot_detected", "challenge_required",
        }
        fatal_symptoms = {
            "crash", "permission_denied", "disk_full",
            "out_of_memory", "invalid_state",
        }
        
        s = symptom.lower().replace("-", "_").replace(" ", "_")
        
        if any(x in s for x in fatal_symptoms):
            return FailureType.FATAL
        if any(x in s for x in antibot_symptoms):
            return FailureType.ANTI_BOT
        if any(x in s for x in policy_symptoms):
            return FailureType.POLICY_GATE
        if any(x in s for x in deterministic_symptoms):
            return FailureType.DETERMINISTIC
        if any(x in s for x in transient_symptoms):
            return FailureType.TRANSIENT
        
        # デフォルト: DETERMINISTIC
        return FailureType.DETERMINISTIC
