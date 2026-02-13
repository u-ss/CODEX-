# -*- coding: utf-8 -*-
"""
Stealth Web Tools — LocalWebTools の drop-in 置換

StealthFetcher + SearchAdapter を使い、BOT判定回避付きの
search_web() / read_url_content() を提供する。

既存の LocalWebTools と同じインターフェースを維持しつつ、
以下の強化を実装:
  - Playwright stealth による BOT 回避
  - Google/Bing 検索結果の構造化パース
  - 全アクセスのログ記録（監査用）
  - ヒューマンシミュレーション

使用例:
    async with StealthWebTools() as tools:
        results = await tools.search_web("AI 自動化 案件")
        content = await tools.read_url_content("https://example.com")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from stealth_fetcher import StealthFetcher, FetchResult, AccessLog
from search_adapter import SearchAdapter, SearchResult

logger = logging.getLogger(__name__)


class StealthWebTools:
    """
    BOT回避付きWebツール — LocalWebToolsの上位互換

    インターフェース:
        search_web(query) -> List[Dict]
        read_url_content(url) -> str

    LocalWebToolsとの違い:
        - Playwright stealth でBOT判定回避
        - Google/Bing実検索（RSS → HTMLパース）
        - UA/Viewport ローテーション
        - アクセスログ自動記録
        - ヒューマンシミュレーション
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 30000,
        max_chars: int = 15000,
        search_engine: str = "duckduckgo",
        max_search_results: int = 10,
        min_delay_sec: float = 1.0,
        max_delay_sec: float = 3.0,
        rate_limit_per_min: int = 20,
        log_dir: Optional[Path] = None,
    ):
        self._fetcher_kwargs = {
            "headless": headless,
            "timeout_ms": timeout_ms,
            "max_chars": max_chars,
            "min_delay_sec": min_delay_sec,
            "max_delay_sec": max_delay_sec,
            "rate_limit_per_min": rate_limit_per_min,
            "log_dir": log_dir,
        }
        self._search_engine = search_engine
        self._max_search_results = max_search_results

        self._fetcher: Optional[StealthFetcher] = None
        self._adapter: Optional[SearchAdapter] = None
        self._search_log: List[Dict[str, Any]] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """ツールを初期化"""
        self._fetcher = StealthFetcher(**self._fetcher_kwargs)
        await self._fetcher.start()
        self._adapter = SearchAdapter(
            self._fetcher,
            engine=self._search_engine,
            max_results=self._max_search_results,
        )

    async def close(self) -> None:
        """ツールを終了"""
        if self._fetcher:
            await self._fetcher.close()

    # --- パブリック API（LocalWebTools互換）---

    async def search_web(self, query: str) -> List[Dict[str, Any]]:
        """
        検索を実行（LocalWebTools.search_web互換）

        Args:
            query: 検索クエリ

        Returns:
            List[Dict]: 検索結果リスト
                - title: タイトル
                - url: URL
                - snippet: スニペット
                - position: 順位
        """
        if not self._adapter:
            raise RuntimeError("StealthWebToolsが未起動")

        start = time.monotonic()
        results = await self._adapter.search(query)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 検索ログ記録
        self._search_log.append({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "query": query,
            "engine": self._search_engine,
            "results_count": len(results),
            "elapsed_ms": elapsed_ms,
        })

        # dict形式で返す（LocalWebTools互換）
        return [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "position": r.position,
            }
            for r in results
        ]

    async def read_url_content(self, url: str) -> str:
        """
        URLの内容をテキストで取得（LocalWebTools.read_url_content互換）

        Args:
            url: 取得対象URL

        Returns:
            str: ページのテキスト内容
        """
        if not self._fetcher:
            raise RuntimeError("StealthWebToolsが未起動")

        result = await self._fetcher.fetch(url)

        if result.bot_detected:
            logger.warning("BOT検出: %s → IDローテーション後にリトライ", url)
            await self._fetcher.rotate_identity()
            result = await self._fetcher.fetch(url)

        return result.text

    # --- 拡張API ---

    async def fetch_multiple(self, urls: List[str]) -> List[FetchResult]:
        """複数URLを順次フェッチ（レートリミッティング付き）"""
        results = []
        for url in urls:
            result = await self._fetcher.fetch(url)
            results.append(result)
        return results

    async def rotate_identity(self) -> None:
        """UA/Viewport を変更"""
        if self._fetcher:
            await self._fetcher.rotate_identity()

    def get_search_log(self) -> List[Dict[str, Any]]:
        """検索ログを取得"""
        return list(self._search_log)

    def get_access_log(self) -> List[Dict[str, Any]]:
        """アクセスログを取得"""
        if self._fetcher:
            return self._fetcher.get_access_log()
        return []

    def get_audit_summary(self) -> Dict[str, Any]:
        """
        監査サマリーを取得（CODEXAPP連携用）

        Returns:
            Dict: 監査メタデータ
        """
        access_log = self.get_access_log()
        search_log = self.get_search_log()

        total_fetches = len(access_log)
        total_searches = len(search_log)
        bot_detections = sum(1 for e in access_log if e.get("bot_detected"))
        errors = sum(1 for e in access_log if e.get("error"))
        total_bytes = sum(e.get("bytes_fetched", 0) for e in access_log)
        avg_elapsed = (
            sum(e.get("elapsed_ms", 0) for e in access_log) / total_fetches
            if total_fetches > 0
            else 0
        )

        return {
            "total_fetches": total_fetches,
            "total_searches": total_searches,
            "bot_detections": bot_detections,
            "bot_detection_rate": f"{bot_detections / total_fetches * 100:.1f}%" if total_fetches else "N/A",
            "errors": errors,
            "error_rate": f"{errors / total_fetches * 100:.1f}%" if total_fetches else "N/A",
            "total_bytes_fetched": total_bytes,
            "avg_elapsed_ms": round(avg_elapsed),
            "unique_user_agents": len(set(e.get("user_agent", "") for e in access_log)),
        }


# --- 同期ラッパー（スクリプト用）---

def create_sync_tools(**kwargs) -> "SyncStealthWebTools":
    """同期版StealthWebToolsを作成"""
    return SyncStealthWebTools(**kwargs)


class SyncStealthWebTools:
    """同期版StealthWebTools（スクリプトから使いやすい）"""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._tools: Optional[StealthWebTools] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def __enter__(self):
        self._loop = asyncio.new_event_loop()
        self._tools = StealthWebTools(**self._kwargs)
        self._loop.run_until_complete(self._tools.start())
        return self

    def __exit__(self, *args):
        if self._tools:
            self._loop.run_until_complete(self._tools.close())
        if self._loop:
            self._loop.close()

    def search_web(self, query: str) -> List[Dict[str, Any]]:
        return self._loop.run_until_complete(self._tools.search_web(query))

    def read_url_content(self, url: str) -> str:
        return self._loop.run_until_complete(self._tools.read_url_content(url))

    def get_audit_summary(self) -> Dict[str, Any]:
        return self._tools.get_audit_summary()

    def get_search_log(self) -> List[Dict[str, Any]]:
        return self._tools.get_search_log()

    def get_access_log(self) -> List[Dict[str, Any]]:
        return self._tools.get_access_log()
