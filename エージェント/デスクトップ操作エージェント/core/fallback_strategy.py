"""
Fallback Strategy - フォールバック戦略

ChatGPT相談（Rally 7）で設計したフォールバック戦略を実装:
- 切替順: UIA → DOM → 画像認識 → 座標クリック
- 閾値: 各手段で失敗N回でフォールバック
- 証拠採取: 切替時に最小証拠を保存
- 履歴学習: 成功パターンを記録
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ResolutionMethod(Enum):
    """解決手段"""
    UIA = "uia"         # UIAutomation（最優先）
    DOM = "dom"         # Playwright/CDP
    IMAGE = "image"     # 画像認識/VLM
    COORD = "coord"     # 座標クリック（最終手段）
    MANUAL = "manual"   # ユーザー手動（フォールバック終点）


# 切替順序（優先度順）
FALLBACK_ORDER = [
    ResolutionMethod.UIA,
    ResolutionMethod.DOM,
    ResolutionMethod.IMAGE,
    ResolutionMethod.COORD,
    ResolutionMethod.MANUAL,
]


@dataclass
class FallbackConfig:
    """フォールバック設定"""
    # 各手段の失敗閾値
    uia_max_failures: int = 3
    dom_max_failures: int = 3
    image_max_failures: int = 2
    coord_max_failures: int = 1
    
    # 証拠採取
    capture_evidence_on_switch: bool = True


@dataclass
class ResolutionAttempt:
    """解決試行記録"""
    method: ResolutionMethod
    success: bool
    timestamp: datetime
    target_key: str
    evidence: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class FallbackEvidence:
    """フォールバック時の証拠"""
    from_method: ResolutionMethod
    to_method: ResolutionMethod
    timestamp: datetime
    reason: str
    target_key: str
    screenshot_path: Optional[str] = None
    uia_dump: Optional[str] = None
    dom_snapshot: Optional[str] = None


class FallbackStrategy:
    """フォールバック戦略"""
    
    def __init__(self, config: Optional[FallbackConfig] = None):
        self.config = config or FallbackConfig()
        self._current_method: Dict[str, ResolutionMethod] = {}  # target_key -> method
        self._failure_counts: Dict[str, Dict[ResolutionMethod, int]] = {}
        self._attempts: List[ResolutionAttempt] = []
        self._evidence_log: List[FallbackEvidence] = []
        self._success_history: Dict[str, List[ResolutionMethod]] = {}  # 成功パターン
    
    def get_current_method(self, target_key: str) -> ResolutionMethod:
        """現在の解決手段を取得"""
        if target_key not in self._current_method:
            self._current_method[target_key] = ResolutionMethod.UIA
        return self._current_method[target_key]
    
    def _get_max_failures(self, method: ResolutionMethod) -> int:
        """手段ごとの失敗閾値"""
        if method == ResolutionMethod.UIA:
            return self.config.uia_max_failures
        elif method == ResolutionMethod.DOM:
            return self.config.dom_max_failures
        elif method == ResolutionMethod.IMAGE:
            return self.config.image_max_failures
        elif method == ResolutionMethod.COORD:
            return self.config.coord_max_failures
        return 1
    
    def _get_failure_count(self, target_key: str, method: ResolutionMethod) -> int:
        """失敗回数を取得"""
        if target_key not in self._failure_counts:
            self._failure_counts[target_key] = {}
        return self._failure_counts[target_key].get(method, 0)
    
    def _increment_failure(self, target_key: str, method: ResolutionMethod):
        """失敗回数をインクリメント"""
        if target_key not in self._failure_counts:
            self._failure_counts[target_key] = {}
        current = self._failure_counts[target_key].get(method, 0)
        self._failure_counts[target_key][method] = current + 1
    
    def _get_next_method(self, current: ResolutionMethod) -> Optional[ResolutionMethod]:
        """次の手段を取得"""
        try:
            idx = FALLBACK_ORDER.index(current)
            if idx + 1 < len(FALLBACK_ORDER):
                return FALLBACK_ORDER[idx + 1]
        except ValueError:
            pass
        return None
    
    def record_attempt(
        self,
        target_key: str,
        method: ResolutionMethod,
        success: bool,
        evidence: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> Optional[ResolutionMethod]:
        """
        試行結果を記録
        
        Returns:
            次に使うべき手段（フォールバック発生時）、またはNone
        """
        attempt = ResolutionAttempt(
            method=method,
            success=success,
            timestamp=datetime.now(),
            target_key=target_key,
            evidence=evidence,
            error=error
        )
        self._attempts.append(attempt)
        
        if success:
            # 成功: カウンタリセット、成功パターン記録
            if target_key in self._failure_counts:
                self._failure_counts[target_key][method] = 0
            
            # 成功履歴に記録
            if target_key not in self._success_history:
                self._success_history[target_key] = []
            if method not in self._success_history[target_key]:
                self._success_history[target_key].append(method)
                logger.info(f"Success pattern recorded: {target_key} -> {method.value}")
            
            return None
        
        # 失敗: カウント増加
        self._increment_failure(target_key, method)
        count = self._get_failure_count(target_key, method)
        max_failures = self._get_max_failures(method)
        
        logger.warning(f"Failure: {method.value} ({count}/{max_failures}) for {target_key}")
        
        # 閾値到達でフォールバック
        if count >= max_failures:
            next_method = self._get_next_method(method)
            if next_method:
                self._trigger_fallback(target_key, method, next_method, error or "threshold reached")
                return next_method
            else:
                logger.error(f"No more fallback options for {target_key}")
                return ResolutionMethod.MANUAL
        
        return None
    
    def _trigger_fallback(
        self,
        target_key: str,
        from_method: ResolutionMethod,
        to_method: ResolutionMethod,
        reason: str
    ):
        """フォールバック発動"""
        self._current_method[target_key] = to_method
        
        evidence = FallbackEvidence(
            from_method=from_method,
            to_method=to_method,
            timestamp=datetime.now(),
            reason=reason,
            target_key=target_key
        )
        self._evidence_log.append(evidence)
        
        logger.info(f"Fallback: {from_method.value} -> {to_method.value} ({reason})")
    
    def force_fallback(self, target_key: str, reason: str = "manual"):
        """強制フォールバック"""
        current = self.get_current_method(target_key)
        next_method = self._get_next_method(current)
        if next_method:
            self._trigger_fallback(target_key, current, next_method, reason)
    
    def reset_to_best(self, target_key: str):
        """成功履歴から最良の手段にリセット"""
        if target_key in self._success_history and self._success_history[target_key]:
            # 最初に成功した手段を使う（最も安定している可能性）
            best = self._success_history[target_key][0]
            self._current_method[target_key] = best
            logger.info(f"Reset to best method: {best.value} for {target_key}")
        else:
            # 履歴なしならUIAにリセット
            self._current_method[target_key] = ResolutionMethod.UIA
    
    def get_recommended_method(self, target_key: str) -> ResolutionMethod:
        """推奨手段を取得（成功履歴があればそれを優先）"""
        if target_key in self._success_history and self._success_history[target_key]:
            return self._success_history[target_key][0]
        return self.get_current_method(target_key)
    
    def get_stats(self) -> Dict[str, Any]:
        """統計情報"""
        return {
            "total_attempts": len(self._attempts),
            "fallback_count": len(self._evidence_log),
            "success_patterns": {k: [m.value for m in v] for k, v in self._success_history.items()},
            "current_methods": {k: v.value for k, v in self._current_method.items()}
        }
    
    def get_evidence_log(self) -> List[FallbackEvidence]:
        """証拠ログ取得"""
        return self._evidence_log.copy()


# 使用例とテスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Fallback Strategy テスト ===")
    
    strategy = FallbackStrategy(FallbackConfig(
        uia_max_failures=2,
        dom_max_failures=2,
        coord_max_failures=1
    ))
    
    target = "button_submit"
    
    # Test 1: 現在手段取得（初期はUIA）
    current = strategy.get_current_method(target)
    print(f"Test 1 - Initial method: {current.value} (expected: uia)")
    
    # Test 2: UIA失敗2回→DOMへフォールバック
    strategy.record_attempt(target, ResolutionMethod.UIA, False, error="not found")
    next_method = strategy.record_attempt(target, ResolutionMethod.UIA, False, error="not found")
    print(f"Test 2 - After UIA 2x fail: {next_method.value if next_method else 'None'} (expected: dom)")
    
    # Test 3: DOM成功→成功パターン記録
    strategy.record_attempt(target, ResolutionMethod.DOM, True)
    stats = strategy.get_stats()
    print(f"Test 3 - Success patterns: {stats['success_patterns']} (expected: {target}: ['dom'])")
    
    # Test 4: 推奨手段
    recommended = strategy.get_recommended_method(target)
    print(f"Test 4 - Recommended: {recommended.value} (expected: dom)")
    
    # Test 5: 強制フォールバック
    strategy.force_fallback(target, "manual test")
    current = strategy.get_current_method(target)
    print(f"Test 5 - After force fallback: {current.value} (expected: image)")
    
    # 結果
    passed = (
        current == ResolutionMethod.IMAGE and
        recommended == ResolutionMethod.DOM
    )
    print(f"\n{'✅ テスト完了' if passed else '❌ 一部失敗'}")
