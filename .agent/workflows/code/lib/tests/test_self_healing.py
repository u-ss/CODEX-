from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.self_healing import CircuitBreaker, CircuitState


class TestCircuitBreakerCooldown(unittest.TestCase):
    """CircuitBreakerのcooldown遷移テスト"""
    
    def test_initial_state_is_closed(self):
        """初期状態はCLOSED"""
        cb = CircuitBreaker(key="test_cmd")
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.allow())
    
    def test_open_after_threshold_failures(self):
        """閾値回数失敗でOPENになる"""
        cb = CircuitBreaker(key="test_cmd", failure_threshold=3)
        
        cb.on_failure()
        cb.on_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        
        cb.on_failure()  # 3回目
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertIsNotNone(cb.next_retry_at)
    
    def test_open_denies_allow_before_cooldown(self):
        """OPEN中はcooldown前ならallow=False"""
        cb = CircuitBreaker(key="test_cmd", failure_threshold=1, cooldown_seconds=60)
        cb.on_failure()
        
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.allow())
    
    def test_open_allows_after_cooldown_expired(self):
        """cooldown経過後はallow=TrueでHALF_OPENに遷移（v4.2.4）"""
        cb = CircuitBreaker(key="test_cmd", failure_threshold=1, cooldown_seconds=0)
        cb.on_failure()
        
        # cooldown=0なので即遷移可能
        # next_retry_atを過去に設定
        cb.next_retry_at = (datetime.now() - timedelta(seconds=1)).isoformat()
        
        self.assertTrue(cb.allow())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
    
    def test_half_open_to_closed_on_success(self):
        """HALF_OPENで成功すればCLOSEDに戻る"""
        cb = CircuitBreaker(key="test_cmd", failure_threshold=1, success_threshold=2)
        cb.on_failure()
        cb.try_half_open()
        
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        
        cb.on_success()
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)  # まだ1回
        
        cb.on_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
    
    def test_half_open_to_open_on_failure(self):
        """HALF_OPENで失敗すればOPENに戻る"""
        cb = CircuitBreaker(key="test_cmd", failure_threshold=1)
        cb.on_failure()
        cb.try_half_open()
        
        cb.on_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)


if __name__ == "__main__":
    unittest.main()
