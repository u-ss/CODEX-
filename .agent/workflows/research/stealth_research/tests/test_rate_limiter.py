# -*- coding: utf-8 -*-
"""RateLimiterのユニットテスト"""
import time
import pytest
from stealth_research.config import RateLimitConfig
from stealth_research.fetch.rate_limiter import RateLimiter


class TestAcquire:
    """レート制限取得のテスト"""

    def test_first_acquire_no_wait(self):
        """初回取得は待機なし"""
        cfg = RateLimitConfig(min_interval_sec=1.0)
        rl = RateLimiter(config=cfg)
        result = rl.acquire("example.com")
        assert result.waited_sec < 0.1

    def test_rapid_acquire_waits(self):
        """連続取得で待機発生"""
        cfg = RateLimitConfig(min_interval_sec=0.2)
        rl = RateLimiter(config=cfg)
        rl.acquire("example.com")
        start = time.time()
        result = rl.acquire("example.com")
        elapsed = time.time() - start
        assert elapsed >= 0.15  # 最低0.15秒は待機

    def test_different_hosts_independent(self):
        """異なるホストは独立"""
        cfg = RateLimitConfig(min_interval_sec=1.0)
        rl = RateLimiter(config=cfg)
        rl.acquire("host1.com")
        result = rl.acquire("host2.com")
        assert result.waited_sec < 0.1


class TestRetryAfter:
    """Retry-After処理のテスト"""

    def test_set_retry_after(self):
        """Retry-After設定が動作"""
        cfg = RateLimitConfig(min_interval_sec=0.05)
        rl = RateLimiter(config=cfg)
        rl.set_retry_after("example.com", 0.3)
        start = time.time()
        result = rl.acquire("example.com")
        elapsed = time.time() - start
        assert elapsed >= 0.2  # Retry-Afterで待機

    def test_retry_after_respected_flag(self):
        """Retry-Afterの尊重フラグ"""
        cfg = RateLimitConfig(min_interval_sec=0.05)
        rl = RateLimiter(config=cfg)
        rl.set_retry_after("example.com", 0.3)
        result = rl.acquire("example.com")
        assert result.retry_after_respected is True


class TestStats:
    """統計のテスト"""

    def test_stats_tracking(self):
        """統計が正しく記録される"""
        cfg = RateLimitConfig(min_interval_sec=0.05)
        rl = RateLimiter(config=cfg)
        rl.acquire("example.com")
        rl.acquire("example.com")
        stats = rl.get_stats()
        assert stats["total_waits"] >= 1
