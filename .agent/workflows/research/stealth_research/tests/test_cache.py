# -*- coding: utf-8 -*-
"""UrlCacheのユニットテスト"""
import time
import pytest
from stealth_research.fetch.cache import UrlCache, CacheEntry


class TestCanonicalizeUrl:
    """URL正規化のテスト"""

    def test_remove_utm_params(self):
        cache = UrlCache()
        url = "https://example.com/page?utm_source=twitter&utm_medium=social&id=42"
        canonical = cache.canonicalize(url)
        assert "utm_source" not in canonical
        assert "utm_medium" not in canonical
        assert "id=42" in canonical

    def test_remove_fragment(self):
        cache = UrlCache()
        url = "https://example.com/page#section1"
        canonical = cache.canonicalize(url)
        assert "#" not in canonical

    def test_normalize_host_case(self):
        cache = UrlCache()
        url = "https://EXAMPLE.COM/Page"
        canonical = cache.canonicalize(url)
        assert "example.com" in canonical

    def test_trailing_slash_normalization(self):
        cache = UrlCache()
        url1 = cache.canonicalize("https://example.com/path/")
        url2 = cache.canonicalize("https://example.com/path")
        assert url1 == url2

    def test_root_slash_preserved(self):
        cache = UrlCache()
        canonical = cache.canonicalize("https://example.com/")
        assert canonical.endswith("/")

    def test_query_param_sorting(self):
        cache = UrlCache()
        url1 = cache.canonicalize("https://example.com?b=2&a=1")
        url2 = cache.canonicalize("https://example.com?a=1&b=2")
        assert url1 == url2


class TestCachePutGet:
    """キャッシュ保存・取得のテスト"""

    def test_put_and_get(self):
        cache = UrlCache()
        cache.put(url="https://example.com/page", content="hello", content_hash="abc123",
                  etag='"etag-val"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
        entry = cache.get("https://example.com/page")
        assert entry is not None
        assert entry.content == "hello"
        assert entry.etag == '"etag-val"'
        assert entry.last_modified == "Mon, 01 Jan 2026 00:00:00 GMT"

    def test_ttl_expiry(self):
        cache = UrlCache(ttl_sec=0.1)
        cache.put(url="https://example.com/page", content="hello")
        time.sleep(0.15)
        entry = cache.get("https://example.com/page")
        assert entry is None

    def test_has(self):
        cache = UrlCache()
        cache.put(url="https://example.com/page", content="c")
        assert cache.has("https://example.com/page") is True
        assert cache.has("https://example.com/other") is False


class TestConditionalHeaders:
    """条件付きGETヘッダーのテスト"""

    def test_etag_header(self):
        cache = UrlCache()
        cache.put(url="https://example.com/p", etag='"abc"')
        headers = cache.get_conditional_headers("https://example.com/p")
        assert headers.get("If-None-Match") == '"abc"'

    def test_last_modified_header(self):
        cache = UrlCache()
        cache.put(url="https://example.com/p", last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
        headers = cache.get_conditional_headers("https://example.com/p")
        assert headers.get("If-Modified-Since") == "Mon, 01 Jan 2026 00:00:00 GMT"

    def test_no_headers_for_uncached(self):
        cache = UrlCache()
        headers = cache.get_conditional_headers("https://example.com/new")
        assert headers == {}


class TestDuplicateDetection:
    """重複URL検出のテスト"""

    def test_duplicate_detection(self):
        cache = UrlCache()
        seen = set()
        assert cache.is_duplicate("https://example.com/page", seen) is False
        assert cache.is_duplicate("https://example.com/page", seen) is True

    def test_canonical_duplicate(self):
        cache = UrlCache()
        seen = set()
        assert cache.is_duplicate("https://example.com/page?utm_source=x", seen) is False
        assert cache.is_duplicate("https://example.com/page", seen) is True

    def test_stats(self):
        cache = UrlCache()
        cache.put(url="https://example.com/a", content="x")
        cache.get("https://example.com/a")  # hit
        cache.get("https://example.com/b")  # miss
        stats = cache.get_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
