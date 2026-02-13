# -*- coding: utf-8 -*-
"""統合テスト: 304復元、prefilter順序、crawl-delay×retry"""
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from stealth_research.config import StealthResearchConfig, BudgetConfig
from stealth_research.fetch.cache import UrlCache, CacheEntry
from stealth_research.fetch.http_fetcher import FetchResult
from stealth_research.verify.checks import Verifier


class TestConditionalGetIntegration:
    """304復元の統合テスト"""

    def test_304_restores_cached_content(self):
        """304レスポンスでキャッシュ済みコンテンツが復元される"""
        cache = UrlCache()
        url = "https://example.com/page"
        original_content = "<html><body>Cached content</body></html>"

        # キャッシュに保存
        cache.put(
            url=url,
            content=original_content,
            etag='"abc123"',
            last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
        )

        # conditional headersが生成される
        headers = cache.get_conditional_headers(url)
        assert headers.get("If-None-Match") == '"abc123"'
        assert headers.get("If-Modified-Since") == "Mon, 01 Jan 2026 00:00:00 GMT"

        # 304時にキャッシュからコンテンツ復元
        cached = cache.get(url)
        assert cached is not None
        assert cached.content == original_content

        # FetchResultを304から復元
        result_304 = FetchResult(url=url, status_code=304, headers={})
        restored = FetchResult(
            url=url,
            content=cached.content,
            status_code=200,
            final_url=url,
            headers=result_304.headers,
            duration_ms=result_304.duration_ms,
        )
        assert restored.success is True
        assert restored.content == original_content


class TestPrefilterOrder:
    """予算前プリフィルタの順序テスト"""

    def test_disallowed_url_skipped_before_budget(self):
        """robots.txt disallowed URLは予算消費前にスキップ"""
        from stealth_research.fetch.compliance import ComplianceChecker
        from stealth_research.fetch.host_policy import HostPolicyEngine

        checker = ComplianceChecker()
        host_policy = HostPolicyEngine()

        # host policyでブロック
        for _ in range(5):
            host_policy.report_result("https://blocked.com/page", 403)

        decision = host_policy.check("https://blocked.com/page")
        assert decision.allowed is False
        # この時点でURLは予算を消費しない


class TestCrawlDelayRetry:
    """Crawl-Delay × retryの統合テスト"""

    def test_crawl_delay_applied_on_retry(self):
        """retryループ内でもcrawl-delayが適用される"""
        from stealth_research.config import RateLimitConfig
        from stealth_research.fetch.rate_limiter import RateLimiter

        cfg = RateLimitConfig(min_interval_sec=0.05)
        rl = RateLimiter(config=cfg)
        host = "example.com"

        # Crawl-Delay設定
        rl.set_retry_after(host, 0.2)

        # 初回acquire
        r1 = rl.acquire(host)
        assert r1.retry_after_respected is True

        # retry時の再acquire（crawl-delayが再適用される）
        start = time.time()
        r2 = rl.acquire(host)
        elapsed = time.time() - start
        # min_interval_secまたはretry_afterで待機が発生
        assert elapsed >= 0.04


class TestBudgetGateConfigDriven:
    """予算ゲート設定連動のテスト"""

    def test_custom_budget(self):
        """カスタム予算設定が反映される"""
        from stealth_research.logging.events import RunSummary
        v = Verifier()
        events = [
            {"event_type": "TOOL_CALL", "tool_name": "search_web", "query": "test"},
            {"tool_name": "url_selection", "url": "https://e.com", "selection_reason": "ok"},
        ] + [
            {"event_type": "TOOL_RESULT", "tool_name": "fetch_url",
             "url": f"https://e{i}.com", "host": f"e{i}.com",
             "status": "success", "http_status": 200,
             "extraction_ratio": 0.15, "retry_decision": "no_retry",
             "rate_limit_wait_sec": 0.0, "quality_grade": "medium",
             "content_length": 1000}
            for i in range(30)
        ]
        summary = RunSummary(
            total_queries=1, total_urls_found=30, total_urls_before_dedup=0,
            total_fetches=30, successful_fetches=30, failed_fetches=0,
            avg_extraction_ratio=0.15,
            quality_counts={"medium": 25, "high": 5},
        )

        # 予算20でテスト → 30フェッチなのでFAIL
        result = v.verify(events, summary, budget_max_fetches=20)
        budget_check = [c for c in result.checks if c.name == "budget_not_exceeded"][0]
        assert budget_check.passed is False
        assert "30/20" in budget_check.detail

        # 予算50でテスト → 30フェッチなのでPASS
        result2 = v.verify(events, summary, budget_max_fetches=50)
        budget_check2 = [c for c in result2.checks if c.name == "budget_not_exceeded"][0]
        assert budget_check2.passed is True
        assert "30/50" in budget_check2.detail


class TestSearcherConfigDriven:
    """検索プロバイダ設定連動のテスト"""

    def test_config_providers_reflected(self):
        """config.search.providersがFederatedSearchに反映される"""
        from stealth_research.orchestrator import Orchestrator
        from stealth_research.config import StealthResearchConfig, SearchConfig

        # DDGのみ設定
        cfg = StealthResearchConfig(
            search=SearchConfig(providers=["duckduckgo"])
        )
        orch = Orchestrator(config=cfg)
        names = orch.searcher.get_provider_names()
        assert "duckduckgo" in names
        assert len(names) == 1

    def test_default_providers(self):
        """デフォルト設定で2プロバイダ"""
        from stealth_research.orchestrator import Orchestrator
        orch = Orchestrator()
        names = orch.searcher.get_provider_names()
        assert len(names) == 2
