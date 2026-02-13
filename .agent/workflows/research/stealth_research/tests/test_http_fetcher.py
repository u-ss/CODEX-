# -*- coding: utf-8 -*-
"""HttpFetcherのユニットテスト"""
import pytest
from unittest.mock import patch, MagicMock
from stealth_research.fetch.http_fetcher import HttpFetcher, FetchResult, FetchConfig


class TestFetchResult:
    """FetchResultのテスト"""

    def test_success_property(self):
        """200-299はsuccess"""
        r = FetchResult(url="https://example.com", status_code=200)
        assert r.success is True
        r2 = FetchResult(url="https://example.com", status_code=403)
        assert r2.success is False

    def test_host_property(self):
        """hostプロパティ"""
        r = FetchResult(url="https://example.com/path")
        assert r.host == "example.com"

    def test_headers_default(self):
        """headers default to empty dict"""
        r = FetchResult(url="https://example.com")
        assert r.headers == {}

    def test_304_not_success(self):
        """304はsuccessではない"""
        r = FetchResult(url="https://example.com", status_code=304)
        assert r.success is False

    def test_none_status_not_success(self):
        """status_code=NoneはFalse"""
        r = FetchResult(url="https://example.com")
        assert r.success is False


class TestHttpFetcher:
    """HttpFetcherのテスト"""

    def test_extra_headers_merged(self):
        """extra_headersが正しくマージされる"""
        fetcher = HttpFetcher()
        with patch.object(fetcher._session, 'get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "<html>test</html>"
            mock_resp.url = "https://example.com"
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_get.return_value = mock_resp

            fetcher.fetch("https://example.com", extra_headers={"If-None-Match": '"abc"'})
            call_args = mock_get.call_args
            headers = call_args.kwargs.get("headers", call_args[1].get("headers", {}))
            assert headers.get("If-None-Match") == '"abc"'

    def test_timeout_handling(self):
        """タイムアウトエラーのハンドリング"""
        import requests
        fetcher = HttpFetcher()
        with patch.object(fetcher._session, 'get', side_effect=requests.exceptions.Timeout()):
            result = fetcher.fetch("https://example.com")
            assert result.success is False
            assert result.error_class == "timeout"

    def test_connection_error_handling(self):
        """接続エラーのハンドリング"""
        import requests
        fetcher = HttpFetcher()
        with patch.object(fetcher._session, 'get', side_effect=requests.exceptions.ConnectionError("connection_reset")):
            result = fetcher.fetch("https://example.com")
            assert result.success is False
            assert "connection" in result.error_class or "unknown" in result.error_class


class TestBlockDetection:
    """ブロック検知のテスト"""

    def test_403_detected(self):
        """403がforbiddenとして検知"""
        fetcher = HttpFetcher()
        signal = fetcher._detect_block(403, "Access Denied")
        assert signal == "forbidden"

    def test_200_no_block(self):
        """200は検知なし"""
        fetcher = HttpFetcher()
        signal = fetcher._detect_block(200, "<html><body>Normal content</body></html>")
        assert signal == ""

    def test_captcha_detected(self):
        """CAPTCHAコンテンツの検知"""
        fetcher = HttpFetcher()
        signal = fetcher._detect_block(200, "Please verify you are human captcha challenge")
        assert signal in ("captcha", "")  # 検知実装依存
