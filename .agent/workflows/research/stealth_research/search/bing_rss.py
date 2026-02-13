# -*- coding: utf-8 -*-
"""
Bing RSS 検索プロバイダー（既存local_web_toolsベース）
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

import requests


@dataclass
class SearchResult:
    """検索結果1件"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    provider: str = ""


class BingRssSearch:
    """Bing RSS検索"""

    ENDPOINT = "https://www.bing.com/search?format=rss&q="

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """
        Bing RSS APIで検索。

        Returns:
            SearchResultのリスト
        """
        url = f"{self.ENDPOINT}{quote_plus(query)}"
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"
            })
            resp.raise_for_status()
            return self._parse_rss(resp.text, max_results)
        except Exception:
            return []

    def _parse_rss(self, xml_text: str, max_results: int) -> List[SearchResult]:
        """RSSをパースして検索結果を抽出"""
        results = []
        # シンプルなXMLパース（xml.etreeで十分）
        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        for item in items[:max_results]:
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)
            if not title_m:
                title_m = re.search(r"<title>(.*?)</title>", item)
            link_m = re.search(r"<link>(.*?)</link>", item)
            desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", item)
            if not desc_m:
                desc_m = re.search(r"<description>(.*?)</description>", item)

            if title_m and link_m:
                results.append(SearchResult(
                    title=title_m.group(1).strip(),
                    url=link_m.group(1).strip(),
                    snippet=(desc_m.group(1).strip() if desc_m else ""),
                    provider="bing_rss",
                ))
        return results
