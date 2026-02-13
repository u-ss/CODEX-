"""
Incident Classifier（インシデント分類器）

目的: 「何が起きたか」を素早く正確に判断

ChatGPT 5.2フィードバック（2026-02-05 Round5）より:
「症状（symptom）と推定原因（root_cause）を分離して付与」

症状例:
- click_no_effect: クリックしたが反応なし
- stale_element: 要素が古くなった
- focus_lost: フォーカスが外れた
- modal_blocking: モーダルがブロック

原因例:
- timing: タイミング問題
- wrong_target: 対象が違う
- permission_dialog: 権限ダイアログ
- navigation_interrupted: ナビゲーション中断
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class Symptom(Enum):
    """症状（観測可能な失敗パターン）"""
    CLICK_NO_EFFECT = "click_no_effect"       # クリックしたが反応なし
    STALE_ELEMENT = "stale_element"           # 要素が古い/消えた
    FOCUS_LOST = "focus_lost"                 # フォーカスが外れた
    MODAL_BLOCKING = "modal_blocking"         # モーダルがブロック
    ELEMENT_NOT_FOUND = "element_not_found"   # 要素が見つからない
    TIMEOUT = "timeout"                       # タイムアウト
    UNEXPECTED_SCREEN = "unexpected_screen"   # 予期しない画面
    INPUT_REJECTED = "input_rejected"         # 入力が拒否された
    NETWORK_ERROR = "network_error"           # ネットワークエラー
    PERMISSION_DENIED = "permission_denied"   # 権限拒否
    UNKNOWN = "unknown"                       # 不明


class RootCause(Enum):
    """推定原因"""
    TIMING = "timing"                             # タイミング問題
    WRONG_TARGET = "wrong_target"                 # 対象が違う
    SELECTOR_CHANGED = "selector_changed"         # セレクタが変わった
    PERMISSION_DIALOG = "permission_dialog"       # 権限ダイアログ
    NAVIGATION_INTERRUPTED = "navigation_interrupted"  # ナビゲーション中断
    NETWORK_DELAY = "network_delay"               # ネットワーク遅延
    ANIMATION_DELAY = "animation_delay"           # アニメーション遅延
    MODAL_OVERLAY = "modal_overlay"               # モーダルオーバーレイ
    FOCUS_STOLEN = "focus_stolen"                 # フォーカス奪取
    STATE_MISMATCH = "state_mismatch"             # 状態不一致
    RESOURCE_BUSY = "resource_busy"               # リソースビジー
    UNKNOWN = "unknown"                           # 不明


class RecoveryCategory(Enum):
    """回復カテゴリ（既存のfallback_strategyと連携）"""
    TRANSIENT = "transient"           # 一時的→リトライ
    DETERMINISTIC = "deterministic"   # 確定的→別アプローチ
    UNSAFE = "unsafe"                 # 危険→中断


@dataclass
class Incident:
    """インシデント（障害情報）"""
    symptom: Symptom
    root_cause: RootCause
    recovery_category: RecoveryCategory
    description: str
    confidence: float           # 診断の確度
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 追加情報
    action_id: Optional[str] = None
    screen_key: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "symptom": self.symptom.value,
            "root_cause": self.root_cause.value,
            "recovery": self.recovery_category.value,
            "description": self.description,
            "confidence": self.confidence,
        }


class IncidentClassifier:
    """インシデント分類器"""
    
    def __init__(self):
        # 症状→原因のマッピング（確率的）
        self.symptom_to_cause: dict[Symptom, list[tuple[RootCause, float]]] = {
            Symptom.CLICK_NO_EFFECT: [
                (RootCause.TIMING, 0.4),
                (RootCause.MODAL_OVERLAY, 0.3),
                (RootCause.ANIMATION_DELAY, 0.2),
                (RootCause.WRONG_TARGET, 0.1),
            ],
            Symptom.STALE_ELEMENT: [
                (RootCause.NAVIGATION_INTERRUPTED, 0.4),
                (RootCause.TIMING, 0.3),
                (RootCause.SELECTOR_CHANGED, 0.3),
            ],
            Symptom.FOCUS_LOST: [
                (RootCause.FOCUS_STOLEN, 0.5),
                (RootCause.MODAL_OVERLAY, 0.3),
                (RootCause.TIMING, 0.2),
            ],
            Symptom.MODAL_BLOCKING: [
                (RootCause.MODAL_OVERLAY, 0.6),
                (RootCause.PERMISSION_DIALOG, 0.3),
                (RootCause.STATE_MISMATCH, 0.1),
            ],
            Symptom.ELEMENT_NOT_FOUND: [
                (RootCause.SELECTOR_CHANGED, 0.4),
                (RootCause.TIMING, 0.3),
                (RootCause.NAVIGATION_INTERRUPTED, 0.3),
            ],
            Symptom.TIMEOUT: [
                (RootCause.NETWORK_DELAY, 0.4),
                (RootCause.RESOURCE_BUSY, 0.3),
                (RootCause.TIMING, 0.3),
            ],
            Symptom.UNEXPECTED_SCREEN: [
                (RootCause.NAVIGATION_INTERRUPTED, 0.5),
                (RootCause.STATE_MISMATCH, 0.3),
                (RootCause.PERMISSION_DIALOG, 0.2),
            ],
        }
        
        # 原因→回復カテゴリ
        self.cause_to_recovery: dict[RootCause, RecoveryCategory] = {
            RootCause.TIMING: RecoveryCategory.TRANSIENT,
            RootCause.ANIMATION_DELAY: RecoveryCategory.TRANSIENT,
            RootCause.NETWORK_DELAY: RecoveryCategory.TRANSIENT,
            RootCause.RESOURCE_BUSY: RecoveryCategory.TRANSIENT,
            RootCause.WRONG_TARGET: RecoveryCategory.DETERMINISTIC,
            RootCause.SELECTOR_CHANGED: RecoveryCategory.DETERMINISTIC,
            RootCause.STATE_MISMATCH: RecoveryCategory.DETERMINISTIC,
            RootCause.NAVIGATION_INTERRUPTED: RecoveryCategory.DETERMINISTIC,
            RootCause.MODAL_OVERLAY: RecoveryCategory.DETERMINISTIC,
            RootCause.FOCUS_STOLEN: RecoveryCategory.TRANSIENT,
            RootCause.PERMISSION_DIALOG: RecoveryCategory.UNSAFE,
            RootCause.UNKNOWN: RecoveryCategory.UNSAFE,
        }
    
    def classify_from_error(
        self,
        error_message: str,
        context: dict = None
    ) -> Incident:
        """エラーメッセージから分類"""
        context = context or {}
        
        # キーワードマッチで症状を特定
        symptom = self._detect_symptom(error_message, context)
        
        # 症状から原因を推定
        root_cause, confidence = self._estimate_cause(symptom, context)
        
        # 回復カテゴリを決定
        recovery = self.cause_to_recovery.get(root_cause, RecoveryCategory.UNSAFE)
        
        return Incident(
            symptom=symptom,
            root_cause=root_cause,
            recovery_category=recovery,
            description=self._generate_description(symptom, root_cause),
            confidence=confidence,
            evidence={"error": error_message, "context": context}
        )
    
    def classify_from_observation(
        self,
        expected: dict,
        actual: dict,
        context: dict = None
    ) -> Incident:
        """期待と実際の差分から分類"""
        context = context or {}
        
        # 差分を分析
        symptom = Symptom.UNKNOWN
        
        if expected.get("screen_key") != actual.get("screen_key"):
            symptom = Symptom.UNEXPECTED_SCREEN
        elif expected.get("element_visible") and not actual.get("element_visible"):
            symptom = Symptom.STALE_ELEMENT
        elif expected.get("focus") != actual.get("focus"):
            symptom = Symptom.FOCUS_LOST
        elif actual.get("modal_visible"):
            symptom = Symptom.MODAL_BLOCKING
        elif expected.get("action_result") == "click" and not actual.get("changed"):
            symptom = Symptom.CLICK_NO_EFFECT
        
        root_cause, confidence = self._estimate_cause(symptom, {**context, **actual})
        recovery = self.cause_to_recovery.get(root_cause, RecoveryCategory.UNSAFE)
        
        return Incident(
            symptom=symptom,
            root_cause=root_cause,
            recovery_category=recovery,
            description=self._generate_description(symptom, root_cause),
            confidence=confidence,
            evidence={"expected": expected, "actual": actual}
        )
    
    def _detect_symptom(self, error_message: str, context: dict) -> Symptom:
        """エラーメッセージから症状を検出"""
        error_lower = error_message.lower()
        
        patterns = {
            Symptom.ELEMENT_NOT_FOUND: ["not found", "no such element", "cannot find", "見つかりません"],
            Symptom.TIMEOUT: ["timeout", "timed out", "タイムアウト"],
            Symptom.STALE_ELEMENT: ["stale", "detached", "removed", "古い"],
            Symptom.MODAL_BLOCKING: ["blocked", "modal", "dialog", "ダイアログ"],
            Symptom.FOCUS_LOST: ["focus", "blur", "フォーカス"],
            Symptom.NETWORK_ERROR: ["network", "connection", "fetch", "ネットワーク"],
            Symptom.PERMISSION_DENIED: ["permission", "denied", "access", "権限"],
        }
        
        for symptom, keywords in patterns.items():
            for kw in keywords:
                if kw in error_lower:
                    return symptom
        
        return Symptom.UNKNOWN
    
    def _estimate_cause(self, symptom: Symptom, context: dict) -> tuple[RootCause, float]:
        """症状から原因を推定"""
        candidates = self.symptom_to_cause.get(symptom, [(RootCause.UNKNOWN, 0.5)])
        
        # コンテキストで調整
        best_cause = candidates[0][0]
        best_confidence = candidates[0][1]
        
        # モーダルが開いていれば
        if context.get("modal_visible"):
            for cause, conf in candidates:
                if cause == RootCause.MODAL_OVERLAY:
                    return cause, min(1.0, conf + 0.3)
        
        # ネットワーク状態
        if context.get("network_busy"):
            for cause, conf in candidates:
                if cause == RootCause.NETWORK_DELAY:
                    return cause, min(1.0, conf + 0.2)
        
        return best_cause, best_confidence
    
    def _generate_description(self, symptom: Symptom, cause: RootCause) -> str:
        """説明文を生成"""
        symptom_desc = {
            Symptom.CLICK_NO_EFFECT: "クリックが反応しませんでした",
            Symptom.STALE_ELEMENT: "要素が古くなりました",
            Symptom.FOCUS_LOST: "フォーカスが外れました",
            Symptom.MODAL_BLOCKING: "モーダルがブロックしています",
            Symptom.ELEMENT_NOT_FOUND: "要素が見つかりません",
            Symptom.TIMEOUT: "タイムアウトしました",
            Symptom.UNEXPECTED_SCREEN: "予期しない画面です",
            Symptom.UNKNOWN: "不明なエラー",
        }
        
        cause_desc = {
            RootCause.TIMING: "タイミングの問題",
            RootCause.WRONG_TARGET: "対象の誤り",
            RootCause.MODAL_OVERLAY: "モーダルオーバーレイ",
            RootCause.NETWORK_DELAY: "ネットワーク遅延",
            RootCause.NAVIGATION_INTERRUPTED: "ナビゲーション中断",
            RootCause.FOCUS_STOLEN: "フォーカス奪取",
            RootCause.UNKNOWN: "原因不明",
        }
        
        s = symptom_desc.get(symptom, str(symptom.value))
        c = cause_desc.get(cause, str(cause.value))
        
        return f"{s}（推定原因: {c}）"
    
    def get_recovery_suggestion(self, incident: Incident) -> str:
        """回復提案を取得"""
        suggestions = {
            RecoveryCategory.TRANSIENT: "リトライ（短い待機後）",
            RecoveryCategory.DETERMINISTIC: "別のアプローチを試行",
            RecoveryCategory.UNSAFE: "中断して確認を求める",
        }
        return suggestions.get(incident.recovery_category, "中断")
    
    def format_incident(self, incident: Incident) -> str:
        """インシデントをフォーマット"""
        icon = {
            RecoveryCategory.TRANSIENT: "🔄",
            RecoveryCategory.DETERMINISTIC: "🔧",
            RecoveryCategory.UNSAFE: "⛔",
        }[incident.recovery_category]
        
        lines = [
            f"{icon} インシデント:",
            f"   症状: {incident.symptom.value}",
            f"   原因: {incident.root_cause.value}",
            f"   確度: {incident.confidence:.0%}",
            f"   説明: {incident.description}",
            f"   回復: {self.get_recovery_suggestion(incident)}",
        ]
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Incident Classifier テスト")
    print("=" * 60)
    
    classifier = IncidentClassifier()
    
    # エラーメッセージから分類
    test_errors = [
        "Element not found: #submit-button",
        "Timeout waiting for selector",
        "Element is stale, DOM has been modified",
        "Click was blocked by modal dialog",
    ]
    
    print("\n--- エラーメッセージからの分類 ---")
    for error in test_errors:
        incident = classifier.classify_from_error(error)
        print(f"\nエラー: {error}")
        print(classifier.format_incident(incident))
    
    # 観測差分から分類
    print("\n--- 観測差分からの分類 ---")
    incident2 = classifier.classify_from_observation(
        expected={"screen_key": "page1", "element_visible": True},
        actual={"screen_key": "page2", "element_visible": False}
    )
    print(classifier.format_incident(incident2))
    
    print("\n" + "=" * 60)
    print("テスト完了")
