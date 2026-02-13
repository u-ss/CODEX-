# -*- coding: utf-8 -*-
"""
コンプライアンス — robots.txt確認＋crawl制約
v3.0: robots.txt解析、クロール速度制御、除外パス判定
"""
from __future__ import annotations

import time
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


class ComplianceChecker:
    """
    robots.txt準拠チェッカー。

    機能:
    - robots.txtキャッシュ（ホスト単位）
    - User-Agentベースのallow/disallow判定
    - Crawl-Delay遵守
    """

    USER_AGENT = "research-bot"

    def __init__(self, cache_ttl_sec: float = 3600.0):
        self.cache_ttl_sec = cache_ttl_sec
        self._robots_cache: Dict[str, _RobotsEntry] = {}
        self._stats = {
            "checks": 0,
            "allowed": 0,
            "disallowed": 0,
            "fetch_errors": 0,
        }

    def is_allowed(self, url: str) -> bool:
        """
        URLがrobots.txtで許可されているか確認。

        Returns:
            True=許可、False=禁止
        """
        self._stats["checks"] += 1
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        entry = self._get_robots(host, parsed.scheme)
        if entry is None:
            # robots.txt取得失敗の場合は許可（寛容モード）
            self._stats["allowed"] += 1
            return True

        allowed = entry.parser.can_fetch(self.USER_AGENT, url)
        if allowed:
            self._stats["allowed"] += 1
        else:
            self._stats["disallowed"] += 1
        return allowed

    def get_crawl_delay(self, url: str) -> float:
        """
        robots.txtのCrawl-Delayを取得。

        Returns:
            遅延秒数（未指定の場合0.0）
        """
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        entry = self._get_robots(host, parsed.scheme)
        if entry and entry.crawl_delay:
            return entry.crawl_delay
        return 0.0

    def _get_robots(self, host: str, scheme: str) -> Optional["_RobotsEntry"]:
        """robots.txtを取得・キャッシュ"""
        entry = self._robots_cache.get(host)
        if entry and (time.time() - entry.fetched_at) < self.cache_ttl_sec:
            return entry

        # robots.txt取得
        robots_url = f"{scheme}://{host}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
            crawl_delay = parser.crawl_delay(self.USER_AGENT) or 0.0
            entry = _RobotsEntry(
                parser=parser,
                crawl_delay=crawl_delay,
                fetched_at=time.time(),
            )
            self._robots_cache[host] = entry
            return entry
        except Exception:
            self._stats["fetch_errors"] += 1
            return None

    def get_stats(self) -> Dict:
        """コンプライアンス統計"""
        return {
            **self._stats,
            "cache_size": len(self._robots_cache),
        }


class _RobotsEntry:
    """robots.txtキャッシュエントリ"""
    def __init__(self, parser: RobotFileParser, crawl_delay: float, fetched_at: float):
        self.parser = parser
        self.crawl_delay = crawl_delay
        self.fetched_at = fetched_at
