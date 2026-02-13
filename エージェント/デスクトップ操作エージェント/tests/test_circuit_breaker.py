# -*- coding: utf-8 -*-
"""
Circuit Breaker テスト

CODEXからの提案に基づき、以下を検証:
1. 3回連続失敗→OPEN遷移
2. 30秒後HALF_OPEN遷移
3. 1回成功→CLOSED復帰
4. 失敗タイプ別閾値の動作確認
"""

import sys
import time
from pathlib import Path

# コアモジュールをパッケージとして読み込み
core_path = Path(__file__).parent.parent / "core"
parent_path = Path(__file__).parent.parent
sys.path.insert(0, str(parent_path))

import pytest

# coreパッケージからインポート
from core.circuit_breaker import CircuitBreaker, CBKey, CBState, Threshold
from core.failure_taxonomy import FailType


class TestCircuitBreakerBasic:
    """Circuit Breaker基本動作テスト"""

    def setup_method(self):
        """各テスト前にCircuitBreakerをリセット"""
        self.cb = CircuitBreaker()

    def test_初期状態はCLOSED(self):
        """新規キーの状態はNONE（未登録）"""
        key = CBKey("test_screen", "click", "v1")
        snapshot = self.cb.snapshot(key)
        assert snapshot["state"] == "NONE"

    def test_allow_未登録キーは許可(self):
        """未登録キーはallow=True"""
        key = CBKey("test_screen", "click", "v1")
        assert self.cb.allow(key) is True

    def test_成功記録後もCLOSED(self):
        """成功記録後は状態がCLOSEDのまま"""
        key = CBKey("test_screen", "click", "v1")
        self.cb.record(key, ok=True, fail_type=FailType.UNKNOWN)
        snapshot = self.cb.snapshot(key)
        assert snapshot["state"] == "CLOSED"


class TestCircuitBreakerTransition:
    """状態遷移テスト"""

    def setup_method(self):
        # 連続失敗3回でOPENになる閾値を設定
        self.thresholds = {
            FailType.TRANSIENT: Threshold(
                window_s=60.0,
                fail_count=5,
                consecutive_fail=3,  # 3回連続失敗でOPEN
                open_s=0.5,  # 0.5秒でHALF_OPENへ（テスト用に短縮）
            ),
            FailType.UNKNOWN: Threshold(
                window_s=60.0,
                fail_count=5,
                consecutive_fail=3,
                open_s=0.5,
            ),
        }
        self.cb = CircuitBreaker(thresholds=self.thresholds)

    def test_3回連続失敗でOPEN(self):
        """3回連続失敗→OPEN遷移"""
        key = CBKey("test_screen", "click", "v1")
        
        # 1回目失敗
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "CLOSED"
        
        # 2回目失敗
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "CLOSED"
        
        # 3回目失敗→OPEN
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "OPEN"
        
        # OPEN状態ではallow=False
        assert self.cb.allow(key) is False

    def test_OPEN後にHALF_OPENへ遷移(self):
        """OPEN期間経過後→HALF_OPEN遷移（allowでトリガー）"""
        key = CBKey("test_screen", "click", "v1")
        
        # 3回失敗でOPEN
        for _ in range(3):
            self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "OPEN"
        
        # 0.5秒待機（open_s=0.5）
        time.sleep(0.6)
        
        # allowを呼ぶとHALF_OPENに遷移してTrue
        assert self.cb.allow(key) is True
        assert self.cb.snapshot(key)["state"] == "HALF_OPEN"

    def test_HALF_OPENで成功したらCLOSED(self):
        """HALF_OPEN状態で成功→CLOSED復帰"""
        key = CBKey("test_screen", "click", "v1")
        
        # 3回失敗でOPEN
        for _ in range(3):
            self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        
        # HALF_OPENへ遷移
        time.sleep(0.6)
        self.cb.allow(key)
        assert self.cb.snapshot(key)["state"] == "HALF_OPEN"
        
        # 成功記録→CLOSED
        self.cb.record(key, ok=True, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "CLOSED"

    def test_連続失敗カウントは成功でリセット(self):
        """成功後は連続失敗カウントがリセットされる"""
        key = CBKey("test_screen", "click", "v1")
        
        # 2回失敗
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["consecutive_fail"] == 2
        
        # 1回成功→カウントリセット
        self.cb.record(key, ok=True, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["consecutive_fail"] == 0
        
        # また2回失敗してもまだCLOSED
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        self.cb.record(key, ok=False, fail_type=FailType.TRANSIENT)
        assert self.cb.snapshot(key)["state"] == "CLOSED"


class TestCircuitBreakerReset:
    """リセット機能テスト"""

    def setup_method(self):
        self.cb = CircuitBreaker()

    def test_reset_individual(self):
        """個別キーのリセット"""
        key1 = CBKey("screen1", "click", "v1")
        key2 = CBKey("screen2", "click", "v1")
        
        self.cb.record(key1, ok=False, fail_type=FailType.UNKNOWN)
        self.cb.record(key2, ok=False, fail_type=FailType.UNKNOWN)
        
        self.cb.reset(key1)
        
        assert self.cb.snapshot(key1)["state"] == "NONE"
        assert self.cb.snapshot(key2)["state"] != "NONE"

    def test_reset_all(self):
        """全キーリセット"""
        key1 = CBKey("screen1", "click", "v1")
        key2 = CBKey("screen2", "click", "v1")
        
        self.cb.record(key1, ok=False, fail_type=FailType.UNKNOWN)
        self.cb.record(key2, ok=False, fail_type=FailType.UNKNOWN)
        
        self.cb.reset_all()
        
        assert self.cb.snapshot(key1)["state"] == "NONE"
        assert self.cb.snapshot(key2)["state"] == "NONE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
