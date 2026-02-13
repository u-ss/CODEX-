# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Circuit Breaker Module
URL/ホスト単位のサーキットブレーカーで403再試行ループを防止

ルール:
- 同一URLで 403/401/404/410 が url_block_threshold回 → URL遮断
- 同一ホストで 403/401 が host_block_threshold回 → ホスト遮断
- 429/5xx は 失敗カウントのみ（遮断せず）
- ラン内メモリのみ（永続化なし）
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional, Tuple
from urllib.parse import urlparse


# URL遮断対象のHTTPステータスコード
_URL_BLOCK_CODES = frozenset({401, 403, 404, 410})

# ホスト遮断対象のHTTPステータスコード（より厳しい条件）
_HOST_BLOCK_CODES = frozenset({401, 403})

# テキストからHTTPステータスコードを推論するパターン
_STATUS_CODE_RE = re.compile(r"\b(40[1-4]|410)\b")


class ResearchUrlCircuitBreaker:
    """
    URL/ホスト単位のサーキットブレーカー。

    同一URLやホストへの繰り返し失敗（403等）を検知し、
    以降のアクセスを遮断して無限ループを防ぐ。
    """

    def __init__(
        self,
        url_block_threshold: int = 2,
        host_block_threshold: int = 5,
    ) -> None:
        self._url_block_threshold = url_block_threshold
        self._host_block_threshold = host_block_threshold

        # URL単位の失敗カウント（遮断対象コードのみ）
        self._url_failures: dict[str, int] = defaultdict(int)

        # ホスト単位の失敗カウント（遮断対象コードのみ）
        self._host_failures: dict[str, int] = defaultdict(int)

        # 遮断済みURL/ホスト
        self._blocked_urls: set[str] = set()
        self._blocked_hosts: set[str] = set()

        # 全失敗カウント（統計用）
        self._total_failures: int = 0

        # URL単位の全試行カウント（max_same_url_attempts算出用）
        self._url_attempts: dict[str, int] = defaultdict(int)

    def should_skip(self, url: str) -> Tuple[bool, str]:
        """
        URLをフェッチすべきかを判定。

        Returns:
            (skip, reason): skipがTrueなら遮断理由を返す
        """
        # URL遮断チェック
        if url in self._blocked_urls:
            return True, f"url_blocked: {url}"

        # ホスト遮断チェック
        host = self._extract_host(url)
        if host and host in self._blocked_hosts:
            return True, f"host_blocked: {host}"

        return False, ""

    def record_success(self, url: str) -> None:
        """
        成功を記録。遮断済みURLの解除はしない（run内永続）。
        """
        # 成功は追跡用に記録のみ。遮断解除はしない。
        pass

    def record_failure(
        self,
        url: str,
        error_code: Optional[int],
        error_text: str,
    ) -> str:
        """
        失敗を記録し、遮断判定を行う。

        Returns:
            action: "url_blocked" / "host_blocked" / "counted" / "ignored"
        """
        self._total_failures += 1
        resolved_code = self._resolve_error_code(error_code, error_text)

        if resolved_code is None:
            # 不明なエラーは遮断対象外
            return "ignored"

        host = self._extract_host(url)
        action = "counted"

        # URL単位の遮断チェック
        if resolved_code in _URL_BLOCK_CODES:
            self._url_attempts[url] += 1
            self._url_failures[url] += 1
            if self._url_failures[url] >= self._url_block_threshold:
                self._blocked_urls.add(url)
                action = "url_blocked"

        # ホスト単位の遮断チェック
        if resolved_code in _HOST_BLOCK_CODES and host:
            self._host_failures[host] += 1
            if self._host_failures[host] >= self._host_block_threshold:
                self._blocked_hosts.add(host)
                action = "host_blocked"

        return action

    def get_stats(self) -> dict:
        """
        統計情報を返す（summary出力用）。
        """
        # max_same_url_attempts: 遮断対象コードでの最大URL単位試行回数
        max_attempts = 0
        for count in self._url_failures.values():
            if count > max_attempts:
                max_attempts = count

        return {
            "blocked_urls": len(self._blocked_urls),
            "blocked_hosts": len(self._blocked_hosts),
            "max_same_url_attempts": max_attempts,
            "total_failures": self._total_failures,
            "blocked_url_list": sorted(self._blocked_urls),
            "blocked_host_list": sorted(self._blocked_hosts),
        }

    @staticmethod
    def _extract_host(url: str) -> str:
        """URLからホスト部分を抽出"""
        try:
            parsed = urlparse(url)
            return parsed.hostname or ""
        except Exception:
            return ""

    @staticmethod
    def _resolve_error_code(
        error_code: Optional[int],
        error_text: str,
    ) -> Optional[int]:
        """
        エラーコードを解決。明示的なコードがなければテキストから推論。
        """
        if error_code is not None:
            return error_code

        # テキストからHTTPステータスコードを推論
        match = _STATUS_CODE_RE.search(error_text)
        if match:
            return int(match.group(1))

        return None
