# -*- coding: utf-8 -*-
"""
ResearchUrlCircuitBreaker テスト（TDD: RED先行）
403再試行ループ抑止の検証
"""

import pytest
import sys
from pathlib import Path

# lib/ をパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.circuit_breaker import ResearchUrlCircuitBreaker


class TestUrlBlocking:
    """同一URL遮断テスト"""

    def test_first_call_not_blocked(self):
        """初回アクセスは遮断しない"""
        cb = ResearchUrlCircuitBreaker()
        skip, reason = cb.should_skip("https://www.zhihu.com/topic/123")
        assert skip is False
        assert reason == ""

    def test_url_blocked_after_two_403(self):
        """同一URLで403が2回 → 3回目はskip"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://www.zhihu.com/topic/19551275"

        # 1回目: 失敗記録
        cb.record_failure(url, 403, "403 Forbidden")
        skip, _ = cb.should_skip(url)
        assert skip is False  # まだ1回目

        # 2回目: 失敗記録 → URL遮断
        action = cb.record_failure(url, 403, "403 Forbidden")
        assert action == "url_blocked"

        # 3回目: should_skipがTrueを返す
        skip, reason = cb.should_skip(url)
        assert skip is True
        assert "url_blocked" in reason

    def test_url_blocked_with_401(self):
        """401も遮断対象"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/private"
        cb.record_failure(url, 401, "401 Unauthorized")
        cb.record_failure(url, 401, "401 Unauthorized")
        skip, _ = cb.should_skip(url)
        assert skip is True

    def test_url_blocked_with_404(self):
        """404も遮断対象"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/notfound"
        cb.record_failure(url, 404, "404 Not Found")
        cb.record_failure(url, 404, "404 Not Found")
        skip, _ = cb.should_skip(url)
        assert skip is True

    def test_429_does_not_block(self):
        """429は失敗カウントのみ、遮断しない"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://api.example.com/data"
        cb.record_failure(url, 429, "429 Too Many Requests")
        cb.record_failure(url, 429, "429 Too Many Requests")
        cb.record_failure(url, 429, "429 Too Many Requests")
        skip, _ = cb.should_skip(url)
        assert skip is False  # 遮断されない

    def test_5xx_does_not_block(self):
        """5xxは失敗カウントのみ、遮断しない"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/error"
        cb.record_failure(url, 500, "500 Internal Server Error")
        cb.record_failure(url, 500, "500 Internal Server Error")
        skip, _ = cb.should_skip(url)
        assert skip is False


class TestHostBlocking:
    """同一ホスト遮断テスト"""

    def test_host_blocked_after_five_403(self):
        """同一ホストで403が5回（別URL） → ホスト遮断"""
        cb = ResearchUrlCircuitBreaker()
        host = "www.zhihu.com"
        for i in range(5):
            url = f"https://{host}/topic/{i}"
            cb.record_failure(url, 403, "403 Forbidden")

        # 新しいURLでもホスト遮断
        skip, reason = cb.should_skip(f"https://{host}/new-topic")
        assert skip is True
        assert "host_blocked" in reason

    def test_different_host_not_affected(self):
        """別ホストは影響を受けない"""
        cb = ResearchUrlCircuitBreaker()
        for i in range(5):
            cb.record_failure(f"https://blocked.com/page/{i}", 403, "403")

        skip, _ = cb.should_skip("https://other-site.com/page")
        assert skip is False

    def test_401_counts_toward_host_block(self):
        """401もホスト遮断カウントに含まれる"""
        cb = ResearchUrlCircuitBreaker()
        host = "private.example.com"
        for i in range(5):
            cb.record_failure(f"https://{host}/page/{i}", 401, "401")

        skip, _ = cb.should_skip(f"https://{host}/another")
        assert skip is True


class TestMixedScenario:
    """正常/異常URL混在テスト"""

    def test_success_urls_continue(self):
        """200系URLは遮断されない"""
        cb = ResearchUrlCircuitBreaker()
        good_url = "https://good-site.com/article"
        bad_url = "https://bad-site.com/blocked"

        cb.record_failure(bad_url, 403, "403")
        cb.record_failure(bad_url, 403, "403")
        cb.record_success(good_url)

        skip_bad, _ = cb.should_skip(bad_url)
        skip_good, _ = cb.should_skip(good_url)
        assert skip_bad is True
        assert skip_good is False

    def test_success_resets_nothing(self):
        """成功してもURL遮断は解除されない（run内永続）"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/page"
        cb.record_failure(url, 403, "403")
        cb.record_failure(url, 403, "403")
        cb.record_success(url)  # 成功しても遮断済み

        skip, _ = cb.should_skip(url)
        assert skip is True  # 一度遮断されたら解除されない


class TestStats:
    """統計出力テスト"""

    def test_get_stats_empty(self):
        """初期状態では統計はゼロ"""
        cb = ResearchUrlCircuitBreaker()
        stats = cb.get_stats()
        assert stats["blocked_urls"] == 0
        assert stats["blocked_hosts"] == 0
        assert stats["max_same_url_attempts"] == 0
        assert stats["total_failures"] == 0

    def test_get_stats_after_failures(self):
        """失敗後の統計が正しい"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://www.zhihu.com/topic/123"
        cb.record_failure(url, 403, "403")
        cb.record_failure(url, 403, "403")

        stats = cb.get_stats()
        assert stats["blocked_urls"] == 1
        assert stats["max_same_url_attempts"] == 2
        assert stats["total_failures"] == 2

    def test_get_stats_host_block(self):
        """ホスト遮断後の統計"""
        cb = ResearchUrlCircuitBreaker()
        for i in range(5):
            cb.record_failure(f"https://blocked.com/p/{i}", 403, "403")

        stats = cb.get_stats()
        assert stats["blocked_hosts"] == 1
        assert stats["total_failures"] == 5


class TestCustomThresholds:
    """カスタム閾値テスト"""

    def test_custom_url_threshold(self):
        """URL遮断閾値をカスタム設定"""
        cb = ResearchUrlCircuitBreaker(url_block_threshold=3)
        url = "https://example.com/page"
        cb.record_failure(url, 403, "403")
        cb.record_failure(url, 403, "403")
        skip, _ = cb.should_skip(url)
        assert skip is False  # 2回ではまだ

        cb.record_failure(url, 403, "403")
        skip, _ = cb.should_skip(url)
        assert skip is True  # 3回で遮断

    def test_custom_host_threshold(self):
        """ホスト遮断閾値をカスタム設定"""
        cb = ResearchUrlCircuitBreaker(host_block_threshold=3)
        for i in range(3):
            cb.record_failure(f"https://example.com/p/{i}", 403, "403")

        skip, _ = cb.should_skip("https://example.com/new")
        assert skip is True  # 3回で遮断


class TestErrorCodeParsing:
    """エラーコード推論テスト"""

    def test_parse_403_from_text(self):
        """error_code=Noneでもテキストから403を推論"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/page"
        cb.record_failure(url, None, "HTTP 403 Forbidden")
        cb.record_failure(url, None, "403 Forbidden")
        skip, _ = cb.should_skip(url)
        assert skip is True

    def test_unknown_error_does_not_block(self):
        """不明なエラーでは遮断しない"""
        cb = ResearchUrlCircuitBreaker()
        url = "https://example.com/page"
        cb.record_failure(url, None, "Connection timeout")
        cb.record_failure(url, None, "Connection timeout")
        skip, _ = cb.should_skip(url)
        assert skip is False
