# -*- coding: utf-8 -*-
"""
検索フェデレーション — マルチソース検索の統合・重複排除・再ランキング
v3.1: Bing RSS + DuckDuckGo の2系統フェデレーション
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Dict, List, Set
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from .bing_rss import BingRssSearch, SearchResult


class SearchProvider(ABC):
    """検索プロバイダの抽象基底クラス"""

    @property
    @abstractmethod
    def name(self) -> str:
        """プロバイダ名"""
        ...

    @abstractmethod
    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """検索実行"""
        ...


class DuckDuckGoSearch(SearchProvider):
    """DuckDuckGo HTML検索"""

    ENDPOINT = "https://html.duckduckgo.com/html/"

    @property
    def name(self) -> str:
        return "duckduckgo"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        import requests
        results = []
        try:
            resp = requests.post(
                self.ENDPOINT,
                data={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=15,
            )
            resp.raise_for_status()
            # HTMLからリンクを抽出
            results = self._parse_html(resp.text, max_results)
        except Exception:
            pass
        return results

    def _parse_html(self, html: str, max_results: int) -> List[SearchResult]:
        """DDG HTMLレスポンスから検索結果をパース"""
        results = []
        # DDGのresult__aクラスからリンク抽出
        links = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for url, title in links[:max_results]:
            # DDGのリダイレクトURLから実URLを抽出
            clean_url = self._extract_real_url(url)
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            if clean_url and clean_title:
                results.append(SearchResult(
                    title=clean_title,
                    url=clean_url,
                    snippet="",
                    provider="duckduckgo",
                ))
        return results

    @staticmethod
    def _extract_real_url(duckduckgo_url: str) -> str:
        """DDGリダイレクトURLから実URLを抽出"""
        if "duckduckgo.com" in duckduckgo_url:
            params = parse_qs(urlparse(duckduckgo_url).query)
            uddg = params.get("uddg", [])
            if uddg:
                return uddg[0]
        return duckduckgo_url


def canonicalize_url(url: str) -> str:
    """
    URL正規化（重複排除用）
    - UTMパラメータ除去
    - fragment除去
    - クエリパラメータ順序正規化
    """
    parsed = urlparse(url)
    # fragment除去
    # UTMパラメータ除去
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in params.items()
        if not k.startswith("utm_") and k not in ("ref", "source", "fbclid", "gclid")
    }
    # ソートして正規化
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path.rstrip("/") if parsed.path != "/" else "/",
        parsed.params,
        sorted_query,
        "",  # fragment除去
    ))
    return normalized


class FederatedSearch:
    """
    マルチソース検索フェデレーション。
    複数プロバイダから検索し、URL正規化で重複排除、ドメイン多様性で再ランキング。
    """

    def __init__(self, providers: list = None):
        if providers is None:
            # デフォルト: Bing RSS + DuckDuckGo
            self.providers = [
                BingRssSearch(),
                DuckDuckGoSearch(),
            ]
        else:
            self.providers = providers

    def search(
        self,
        query: str,
        max_results: int = 10,
        max_per_domain: int = 3,
    ) -> List[SearchResult]:
        """
        フェデレーション検索。

        Args:
            query: 検索クエリ
            max_results: 最終結果の最大数
            max_per_domain: ドメインあたりの最大URL数

        Returns:
            重複排除・再ランキング済みのSearchResultリスト
        """
        all_results: List[SearchResult] = []
        seen_canonical: Set[str] = set()

        # 各プロバイダから検索
        for provider in self.providers:
            try:
                results = provider.search(query, max_results=max_results)
                for r in results:
                    canonical = canonicalize_url(r.url)
                    if canonical not in seen_canonical:
                        seen_canonical.add(canonical)
                        all_results.append(r)
            except Exception:
                continue

        # ドメイン多様性で再ランキング
        return self._rerank(all_results, max_results, max_per_domain)

    def _rerank(
        self,
        results: List[SearchResult],
        max_results: int,
        max_per_domain: int,
    ) -> List[SearchResult]:
        """
        ドメイン多様性を考慮した再ランキング。
        ラウンドロビンでドメインを分散させる。
        """
        domain_count: Dict[str, int] = {}
        ranked = []

        for r in results:
            domain = urlparse(r.url).netloc.lower()
            count = domain_count.get(domain, 0)
            if count >= max_per_domain:
                continue
            domain_count[domain] = count + 1
            ranked.append(r)
            if len(ranked) >= max_results:
                break

        return ranked

    def get_provider_names(self) -> List[str]:
        """プロバイダ名一覧"""
        return [
            p.name if hasattr(p, 'name') else type(p).__name__
            for p in self.providers
        ]
