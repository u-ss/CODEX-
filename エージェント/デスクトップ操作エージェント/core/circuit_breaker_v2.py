"""
Circuit Breaker V2 - 局所/全体サーキットブレーカ拡張版

ChatGPT相談（Rally 3, 5）で設計した拡張機能を追加:
- 失敗カテゴリ別閾値（同一要素N=2、同一手段N=3、座標クリックN=1）
- タイムウィンドウ（60秒で3回）
- Unknown状態全体（N=5）
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class FailureCategory(Enum):
    """失敗カテゴリ"""
    SAME_ELEMENT = "same_element"      # 同一要素: N=2
    SAME_METHOD = "same_method"        # 同一手段: N=3
    COORD_CLICK = "coord_click"        # 座標クリック: N=1
    UNKNOWN_TOTAL = "unknown_total"    # Unknown状態全体: N=5


@dataclass
class CBConfigV2:
    """サーキットブレーカ設定V2"""
    # 局所閾値
    same_element_max: int = 2
    same_method_max: int = 3
    coord_click_max: int = 1
    
    # 全体閾値
    unknown_total_max: int = 5
    
    # タイムウィンドウ
    time_window_seconds: int = 60
    time_window_max: int = 3
    
    # リカバリ
    recovery_timeout_seconds: int = 30


@dataclass
class FailureRecord:
    """失敗記録"""
    timestamp: datetime
    category: FailureCategory
    target_key: str


class CircuitBreakerV2:
    """サーキットブレーカ拡張版"""
    
    # カテゴリ別閾値マッピング
    CATEGORY_THRESHOLDS = {
        FailureCategory.SAME_ELEMENT: 2,
        FailureCategory.SAME_METHOD: 3,
        FailureCategory.COORD_CLICK: 1,
        FailureCategory.UNKNOWN_TOTAL: 5,
    }
    
    def __init__(self, config: Optional[CBConfigV2] = None):
        self.config = config or CBConfigV2()
        self._failures: List[FailureRecord] = []
        self._open_keys: Dict[str, datetime] = {}
        self._global_open: Optional[datetime] = None
        
        # カテゴリ別閾値を設定から更新
        self.CATEGORY_THRESHOLDS[FailureCategory.SAME_ELEMENT] = self.config.same_element_max
        self.CATEGORY_THRESHOLDS[FailureCategory.SAME_METHOD] = self.config.same_method_max
        self.CATEGORY_THRESHOLDS[FailureCategory.COORD_CLICK] = self.config.coord_click_max
        self.CATEGORY_THRESHOLDS[FailureCategory.UNKNOWN_TOTAL] = self.config.unknown_total_max
    
    def record_failure(
        self,
        category: FailureCategory,
        target_key: str
    ) -> bool:
        """
        失敗を記録
        
        Returns:
            True: CBがOPENになった, False: まだCLOSED
        """
        now = datetime.now()
        self._failures.append(FailureRecord(
            timestamp=now,
            category=category,
            target_key=target_key
        ))
        
        # カテゴリ別カウント
        key = f"{category.value}:{target_key}"
        count = sum(1 for f in self._failures if f.category == category and f.target_key == target_key)
        threshold = self.CATEGORY_THRESHOLDS.get(category, 3)
        
        if count >= threshold:
            self._open_keys[key] = now
            logger.warning(f"CB OPEN: {key} ({count}/{threshold})")
            return True
        
        # タイムウィンドウチェック
        window_start = now - timedelta(seconds=self.config.time_window_seconds)
        recent = [f for f in self._failures if f.timestamp > window_start]
        
        if len(recent) >= self.config.time_window_max:
            self._global_open = now
            logger.error(f"Global CB OPEN: {len(recent)} failures in {self.config.time_window_seconds}s")
            return True
        
        return False
    
    def record_success(self, category: FailureCategory, target_key: str):
        """成功を記録（該当キーをリセット）"""
        key = f"{category.value}:{target_key}"
        if key in self._open_keys:
            del self._open_keys[key]
            logger.info(f"CB reset: {key}")
        
        # 該当する失敗記録を削除
        self._failures = [
            f for f in self._failures
            if not (f.category == category and f.target_key == target_key)
        ]
    
    def can_proceed(self, category: FailureCategory, target_key: str) -> bool:
        """操作を続行できるか"""
        # グローバルチェック
        if self._global_open:
            elapsed = (datetime.now() - self._global_open).total_seconds()
            if elapsed < self.config.recovery_timeout_seconds:
                return False
            self._global_open = None
        
        # ローカルチェック
        key = f"{category.value}:{target_key}"
        if key in self._open_keys:
            elapsed = (datetime.now() - self._open_keys[key]).total_seconds()
            if elapsed < self.config.recovery_timeout_seconds:
                return False
            del self._open_keys[key]
        
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """状態取得"""
        return {
            "global_open": self._global_open is not None,
            "open_keys": list(self._open_keys.keys()),
            "recent_failures": len(self._failures)
        }
    
    def reset(self):
        """リセット"""
        self._failures.clear()
        self._open_keys.clear()
        self._global_open = None


# 使用例とテスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Circuit Breaker V2 テスト ===")
    
    cb = CircuitBreakerV2(CBConfigV2(
        same_element_max=2,
        coord_click_max=1
    ))
    
    # テスト1: 同一要素2回失敗→OPEN
    print("\n--- Test 1: Same Element ---")
    opened1 = cb.record_failure(FailureCategory.SAME_ELEMENT, "btn1")
    print(f"Failure 1: opened={opened1}")
    opened2 = cb.record_failure(FailureCategory.SAME_ELEMENT, "btn1")
    print(f"Failure 2: opened={opened2} (expected: True)")
    
    # テスト2: 座標クリック1回→OPEN
    print("\n--- Test 2: Coord Click ---")
    opened3 = cb.record_failure(FailureCategory.COORD_CLICK, "xy100")
    print(f"Failure: opened={opened3} (expected: True)")
    
    # テスト3: can_proceed
    print("\n--- Test 3: Can Proceed ---")
    can1 = cb.can_proceed(FailureCategory.SAME_ELEMENT, "btn1")
    can2 = cb.can_proceed(FailureCategory.SAME_ELEMENT, "btn2")
    print(f"btn1: {can1} (expected: False)")
    print(f"btn2: {can2} (expected: True)")
    
    # テスト4: 成功でリセット
    print("\n--- Test 4: Success Reset ---")
    cb.record_success(FailureCategory.SAME_ELEMENT, "btn1")
    can3 = cb.can_proceed(FailureCategory.SAME_ELEMENT, "btn1")
    print(f"btn1 after success: {can3} (expected: True)")
    
    # 結果
    passed = opened2 and opened3 and not can1 and can2 and can3
    print(f"\n{'✅ テスト完了' if passed else '❌ 一部失敗'}")
