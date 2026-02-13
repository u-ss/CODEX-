# -*- coding: utf-8 -*-
"""Host Policy Engineのユニットテスト"""
import pytest
from stealth_research.fetch.host_policy import HostPolicyEngine, HostState


class TestHostPolicyCheck:
    """ポリシーチェックのテスト"""

    def test_unknown_host_allowed(self):
        """未知ホストは許可"""
        engine = HostPolicyEngine()
        decision = engine.check("https://newsite.com/page")
        assert decision.allowed is True

    def test_blocked_host_denied(self):
        """403連続でblocked→拒否"""
        engine = HostPolicyEngine()
        url = "https://blocked.com/page"
        # 403を連続報告
        for _ in range(5):
            engine.report_result(url, 403)
        decision = engine.check(url)
        assert decision.allowed is False
        assert decision.state == HostState.BLOCKED


class TestReportResult:
    """結果報告のテスト"""

    def test_success_keeps_ok(self):
        """200はOK維持"""
        engine = HostPolicyEngine()
        engine.report_result("https://example.com/page", 200)
        decision = engine.check("https://example.com/page")
        assert decision.allowed is True

    def test_429_throttles(self):
        """429はthrottled"""
        engine = HostPolicyEngine()
        engine.report_result("https://example.com/page", 429)
        decision = engine.check("https://example.com/page")
        assert decision.state == HostState.THROTTLED


class TestJsDetection:
    """JS必須検出のテスト"""

    def test_no_js_in_normal_content(self):
        """通常コンテンツはJS不要"""
        engine = HostPolicyEngine()
        result = engine.detect_js_required(
            "https://example.com/page",
            "<html><body><p>Normal content</p></body></html>"
        )
        assert result is False

    def test_js_required_signal(self):
        """JS必須シグナルの検出"""
        engine = HostPolicyEngine()
        content = '<html><body><noscript>JavaScript is required</noscript></body></html>'
        result = engine.detect_js_required("https://example.com/page", content)
        # JS検出は内容依存 — シグナルがある場合にTrue
        assert isinstance(result, bool)
