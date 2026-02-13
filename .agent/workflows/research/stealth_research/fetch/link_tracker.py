# -*- coding: utf-8 -*-
"""
リンク追跡 — ページ内リンクの関連度判定＋深掘りURL抽出
v3.0: relevance scoreベースのフィルタ、予算制御付き
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Set
from urllib.parse import urljoin, urlparse


@dataclass
class TrackedLink:
    """追跡対象リンク"""
    url: str
    anchor_text: str = ""
    relevance_score: float = 0.0
    source_url: str = ""


class LinkTracker:
    """
    ページ内リンクを解析し、関連度の高いリンクのみを追跡候補として抽出。

    機能:
    - HTMLからリンク抽出
    - クエリベースのrelevance score計算
    - max_per_page / max_per_host で暴走防止
    - 外部リンクのみ追跡（同一ドメイン内リンクはオプション）
    """

    # 除外するURL拡張子
    _SKIP_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".tar", ".gz", ".rar", ".7z",
        ".mp3", ".mp4", ".avi", ".mov", ".wmv",
        ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    }

    # 除外するURLパターン
    _SKIP_PATTERNS = [
        r'/login', r'/signup', r'/register', r'/cart', r'/checkout',
        r'/privacy', r'/terms', r'/cookie', r'/contact',
        r'javascript:', r'mailto:', r'tel:', r'#$',
    ]

    def __init__(
        self,
        max_per_page: int = 5,
        max_per_host: int = 3,
        min_relevance: float = 0.3,
        follow_same_domain: bool = True,
    ):
        self.max_per_page = max_per_page
        self.max_per_host = max_per_host
        self.min_relevance = min_relevance
        self.follow_same_domain = follow_same_domain

    def extract_links(
        self,
        html: str,
        source_url: str,
        query_keywords: List[str],
        already_seen: Set[str],
    ) -> List[TrackedLink]:
        """
        HTMLからリンクを抽出し、relevance scoreで選別。

        Args:
            html: HTMLコンテンツ
            source_url: 元ページのURL
            query_keywords: 検索クエリのキーワード（関連度計算用）
            already_seen: 既に取得済みのURL集合

        Returns:
            関連度の高いTrackedLinkのリスト
        """
        if not html or not source_url:
            return []

        source_domain = urlparse(source_url).netloc.lower()
        raw_links = self._extract_raw_links(html, source_url)

        scored = []
        host_count = {}

        for url, anchor in raw_links:
            # 既知URLスキップ
            if url in already_seen:
                continue

            # 拡張子フィルタ
            parsed = urlparse(url)
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in self._SKIP_EXTENSIONS):
                continue

            # パターンフィルタ
            if any(re.search(p, url, re.IGNORECASE) for p in self._SKIP_PATTERNS):
                continue

            # 同一ドメインフィルタ
            link_domain = parsed.netloc.lower()
            if not self.follow_same_domain and link_domain == source_domain:
                continue

            # ホスト数制限
            hc = host_count.get(link_domain, 0)
            if hc >= self.max_per_host:
                continue

            # relevance score計算
            score = self._compute_relevance(url, anchor, query_keywords)
            if score < self.min_relevance:
                continue

            host_count[link_domain] = hc + 1
            scored.append(TrackedLink(
                url=url,
                anchor_text=anchor,
                relevance_score=score,
                source_url=source_url,
            ))

        # スコア順でソート → ページあたり上限
        scored.sort(key=lambda x: x.relevance_score, reverse=True)
        return scored[:self.max_per_page]

    def _extract_raw_links(self, html: str, base_url: str) -> List[tuple]:
        """HTMLからリンク(URL, anchor_text)を抽出"""
        links = []
        # <a href="...">text</a> を抽出
        pattern = re.compile(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in pattern.finditer(html):
            href = match.group(1).strip()
            anchor = re.sub(r'<[^>]+>', '', match.group(2)).strip()

            # 相対URL → 絶対URL
            if href.startswith("http"):
                absolute = href
            elif href.startswith("//"):
                absolute = "https:" + href
            elif href.startswith("/"):
                absolute = urljoin(base_url, href)
            else:
                absolute = urljoin(base_url, href)

            if absolute.startswith("http"):
                links.append((absolute, anchor))

        return links

    def _compute_relevance(
        self,
        url: str,
        anchor_text: str,
        keywords: List[str],
    ) -> float:
        """
        リンクの関連度スコアを計算（0.0〜1.0）。
        キーワードがURL/アンカーテキストにどれだけ含まれるかで判定。
        """
        if not keywords:
            return 0.5  # キーワードなしなら中立

        url_lower = url.lower()
        anchor_lower = anchor_text.lower()
        combined = url_lower + " " + anchor_lower

        matches = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in combined:
                matches += 1

        # スコア = マッチしたキーワード率
        base_score = matches / len(keywords) if keywords else 0.0

        # アンカーテキストが長い（情報量が多い）ほどボーナス
        if len(anchor_text) > 30:
            base_score += 0.1
        if len(anchor_text) > 50:
            base_score += 0.1

        return min(1.0, base_score)
