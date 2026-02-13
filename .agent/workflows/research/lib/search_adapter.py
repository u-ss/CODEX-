# -*- coding: utf-8 -*-
"""
Search Adapter — ハイブリッド検索エンジンアダプタ

検索部分: Bing RSS API（requests、BOT検出なし）
ページフェッチ: StealthFetcher（Playwright stealth、BOT回避）

このハイブリッドアプローチにより:
  - 検索エンジンのCAPTCHA/BOT検出を完全回避
  - 個別ページのスクレイピングはBOT回避付きで実行
"""

from __future__ import annotations

import base64
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 検索用UA（requests用、ブラウザ風）
_SEARCH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


@dataclass
class SearchResult:
    """検索結果1件のデータ"""
    title: str
    url: str
    snippet: str
    position: int


class SearchAdapter:
    """
    ハイブリッド検索エンジンアダプタ

    検索: Bing RSS API（requests）→ BOT検出されない
    ページフェッチ: StealthFetcher（Playwright stealth）→ BOT回避

    使用方法:
        adapter = SearchAdapter(fetcher)
        results = await adapter.search("AI automation freelance")
    """

    def __init__(self, fetcher=None, *, max_results: int = 15, timeout_sec: int = 20):
        """
        Args:
            fetcher: StealthFetcher（ページフェッチ用、検索には不使用）
            max_results: 最大取得件数
            timeout_sec: 検索リクエストのタイムアウト
        """
        self.fetcher = fetcher
        self.max_results = max_results
        self.timeout_sec = timeout_sec

    async def search(self, query: str) -> List[SearchResult]:
        """
        Bing RSS APIで検索（非同期だが実際はrequests同期実行）

        Args:
            query: 検索クエリ

        Returns:
            List[SearchResult]: 検索結果リスト
        """
        query = (query or "").strip()
        if not query:
            return []

        # Bing RSS検索（メイン）
        results = self._search_bing_rss(query)

        if not results:
            # HTML直接パースにフォールバック（StealthFetcher使用）
            if self.fetcher:
                logger.info("Bing RSS失敗。Playwright経由でBing HTMLフォールバック")
                results = await self._search_bing_html(query)

        return results

    def _search_bing_rss(self, query: str) -> List[SearchResult]:
        """Bing RSS API で検索（requests使用、BOT検出なし）"""
        try:
            resp = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"},
                timeout=self.timeout_sec,
                headers={"User-Agent": _SEARCH_UA},
            )
            resp.raise_for_status()

            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return []

            results: List[SearchResult] = []
            position = 0
            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                url = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()

                if not url or not title:
                    continue

                # HTMLタグを除去
                desc = re.sub(r"<[^>]+>", "", desc).strip()

                position += 1
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=desc[:300],
                    position=position,
                ))

                if len(results) >= self.max_results:
                    break

            logger.info("Bing RSS検索: '%s' → %d件", query[:50], len(results))
            return results

        except Exception as e:
            logger.warning("Bing RSS検索失敗: %s", e)
            return []

    async def _search_bing_html(self, query: str) -> List[SearchResult]:
        """Bing HTML検索（StealthFetcher経由、フォールバック用）"""
        if not self.fetcher:
            return []

        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.bing.com/search?q={encoded}&setlang=ja"

        result = await self.fetcher.fetch(url)
        if result.error:
            logger.error("Bing HTML検索失敗: %s", result.error)
            return []

        return self._parse_bing_html(result.html)

    def _parse_bing_html(self, html: str) -> List[SearchResult]:
        """Bing HTMLの検索結果をパース"""
        soup = BeautifulSoup(html, "html.parser")
        results: List[SearchResult] = []
        position = 0

        for li in soup.select("li.b_algo"):
            link = li.select_one("h2 a")
            if not link:
                continue

            href = link.get("href", "")
            title = link.get_text(strip=True)

            # BingリダイレクトURLを解決
            actual_url = self._resolve_bing_redirect(href)

            snippet_el = li.select_one("p, div.b_caption p")
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""

            if title and actual_url:
                position += 1
                results.append(SearchResult(
                    title=title,
                    url=actual_url,
                    snippet=snippet[:300],
                    position=position,
                ))

            if len(results) >= self.max_results:
                break

        return results

    @staticmethod
    def _resolve_bing_redirect(href: str) -> Optional[str]:
        """BingリダイレクトURL（bing.com/ck/a）を実URLに解決"""
        if "bing.com/ck/a" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            u_param = parsed.get("u", [None])[0]
            if u_param:
                try:
                    b64 = u_param
                    if b64.startswith("a1"):
                        b64 = b64[2:]
                    b64 += "=" * (-len(b64) % 4)
                    decoded = base64.urlsafe_b64decode(b64).decode("utf-8")
                    if decoded.startswith("http"):
                        return decoded
                except Exception:
                    pass
        if href.startswith("http"):
            return href
        return None
