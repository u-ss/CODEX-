# -*- coding: utf-8 -*-
"""Verifier（checks.py）のユニットテスト"""
import pytest
from stealth_research.verify.checks import Verifier, VerificationResult
from stealth_research.logging.events import RunSummary


def _make_summary(**kwargs):
    """RunSummary生成ヘルパー"""
    defaults = dict(
        run_id="test",
        start_time=0.0,
        end_time=1.0,
        total_queries=1,
        total_urls_found=5,
        total_urls_before_dedup=0,
        total_fetches=5,
        successful_fetches=5,
        failed_fetches=0,
        skipped_fetches=0,
        breaker_opens=0,
        total_rate_limit_waits=0,
        total_rate_limit_wait_sec=0.0,
        avg_extraction_ratio=0.15,
        truncated_count=2,
        quality_counts={"high": 3, "medium": 2},
    )
    defaults.update(kwargs)
    return RunSummary(**defaults)


def _make_fetch_event(url="https://example.com", status="success", http_status=200,
                      extraction_ratio=0.15, retry_decision="no_retry",
                      rate_limit_wait_sec=0.5, quality_grade="medium",
                      content_length=1000, **extra):
    """fetchイベント生成ヘルパー"""
    event = {
        "event_type": "TOOL_RESULT",
        "tool_name": "fetch_url",
        "url": url,
        "host": "example.com",
        "status": status,
        "http_status": http_status,
        "extraction_ratio": extraction_ratio,
        "retry_decision": retry_decision,
        "rate_limit_wait_sec": rate_limit_wait_sec,
        "quality_grade": quality_grade,
        "content_length": content_length,
    }
    event.update(extra)
    return event


def _make_search_event(query="test"):
    return {
        "event_type": "TOOL_CALL",
        "tool_name": "search_web",
        "query": query,
    }


def _make_selection_event(url="https://example.com", reason="selected"):
    return {
        "tool_name": "url_selection",
        "url": url,
        "selection_reason": reason,
    }


class TestVerifierAllPass:
    """全チェック通過のテスト"""

    def test_all_checks_pass(self):
        """正常系: 全10チェック通過"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(),
            _make_fetch_event(url="https://other.com"),
            _make_fetch_event(url="https://third.com"),
        ]
        summary = _make_summary()
        v = Verifier()
        result = v.verify(events, summary)
        assert result.verified_success is True
        assert "10/10" in result.summary


class TestExtractionQualityGate:
    """抽出品質ゲート（v3.1閾値型）のテスト"""

    def test_low_extraction_ratio_fails(self):
        """抽出率3%未満でFAIL"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(extraction_ratio=0.01),
        ]
        summary = _make_summary(avg_extraction_ratio=0.01)
        v = Verifier()
        result = v.verify(events, summary)
        # extraction_quality_loggedがFAILなのでverified_success=False
        quality_check = [c for c in result.checks if c.name == "extraction_quality_logged"][0]
        assert quality_check.passed is False
        assert "抽出率低" in quality_check.detail

    def test_high_empty_ratio_fails(self):
        """empty比率50%超でFAIL"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(extraction_ratio=0.10),
            _make_fetch_event(extraction_ratio=0.10),
        ]
        summary = _make_summary(
            avg_extraction_ratio=0.10,
            quality_counts={"empty": 3, "low": 1, "medium": 1},
        )
        v = Verifier()
        result = v.verify(events, summary)
        quality_check = [c for c in result.checks if c.name == "extraction_quality_logged"][0]
        assert quality_check.passed is False
        assert "empty過多" in quality_check.detail

    def test_good_quality_passes(self):
        """正常品質で通過"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(extraction_ratio=0.15),
        ]
        summary = _make_summary(
            avg_extraction_ratio=0.15,
            quality_counts={"high": 2, "medium": 2, "empty": 1},
        )
        v = Verifier()
        result = v.verify(events, summary)
        quality_check = [c for c in result.checks if c.name == "extraction_quality_logged"][0]
        assert quality_check.passed is True


class TestBreakerCheck:
    """ブレーカーチェックのテスト"""

    def test_no_breaker_is_ok(self):
        """ブレーカー未発動はOK"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(),
        ]
        summary = _make_summary()
        v = Verifier()
        result = v.verify(events, summary)
        breaker = [c for c in result.checks if c.name == "breaker_honored"][0]
        assert breaker.passed is True


class Test403Check:
    """403チェックのテスト"""

    def test_excessive_403_fails(self):
        """同一URL 403が3回以上でFAIL"""
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(http_status=403, status="failed", retry_decision="retry"),
            _make_fetch_event(http_status=403, status="failed", retry_decision="retry"),
            _make_fetch_event(http_status=403, status="failed", retry_decision="no_retry"),
        ]
        summary = _make_summary(successful_fetches=0, failed_fetches=3)
        v = Verifier()
        result = v.verify(events, summary)
        loop_check = [c for c in result.checks if c.name == "max_same_url_403_streak_le_2"][0]
        assert loop_check.passed is False


class TestBudgetCheck:
    """予算チェックのテスト"""

    def test_within_budget(self):
        """予算内は通過"""
        events = [
            _make_search_event(),
            _make_selection_event(),
        ] + [_make_fetch_event(url=f"https://e{i}.com") for i in range(50)]
        summary = _make_summary()
        v = Verifier()
        result = v.verify(events, summary)
        budget = [c for c in result.checks if c.name == "budget_not_exceeded"][0]
        assert budget.passed is True


class TestToDict:
    """to_dict出力のテスト"""

    def test_to_dict_structure(self):
        events = [
            _make_search_event(),
            _make_selection_event(),
            _make_fetch_event(),
        ]
        summary = _make_summary()
        v = Verifier()
        result = v.verify(events, summary)
        d = result.to_dict()
        assert "verified_success" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)
        assert all("name" in c and "passed" in c for c in d["checks"])
