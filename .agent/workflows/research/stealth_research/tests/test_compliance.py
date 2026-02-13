# -*- coding: utf-8 -*-
"""ComplianceCheckerのユニットテスト"""
import pytest
from unittest.mock import patch, MagicMock
from stealth_research.fetch.compliance import ComplianceChecker


class TestIsAllowed:
    """robots.txt判定のテスト"""

    def test_allowed_on_fetch_error(self):
        """robots.txt取得失敗時は寛容モード（許可）"""
        checker = ComplianceChecker()
        with patch.object(checker, '_get_robots', return_value=None):
            assert checker.is_allowed("https://example.com/page") is True

    def test_stats_tracking(self):
        """統計が正しく記録される"""
        checker = ComplianceChecker()
        with patch.object(checker, '_get_robots', return_value=None):
            checker.is_allowed("https://example.com/a")
            checker.is_allowed("https://example.com/b")
        stats = checker.get_stats()
        assert stats["checks"] == 2
        assert stats["allowed"] == 2


class TestGetCrawlDelay:
    """Crawl-Delay取得のテスト"""

    def test_no_delay_when_not_set(self):
        """Crawl-Delay未設定時は0.0"""
        checker = ComplianceChecker()
        with patch.object(checker, '_get_robots', return_value=None):
            delay = checker.get_crawl_delay("https://example.com/page")
        assert delay == 0.0

    def test_delay_from_robots(self):
        """robots.txtにCrawl-Delayがある場合の取得"""
        checker = ComplianceChecker()
        mock_entry = MagicMock()
        mock_entry.crawl_delay = 2.0
        with patch.object(checker, '_get_robots', return_value=mock_entry):
            delay = checker.get_crawl_delay("https://example.com/page")
        assert delay == 2.0


class TestCaching:
    """robots.txtキャッシュのテスト"""

    def test_cache_stats(self):
        """キャッシュサイズが統計に反映"""
        checker = ComplianceChecker()
        stats = checker.get_stats()
        assert stats["cache_size"] == 0
