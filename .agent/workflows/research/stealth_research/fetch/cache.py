# -*- coding: utf-8 -*-
"""
URLキャッシュ — 同一URL再取得防止＋条件付きGET
v3.0: URL正規化、TTLキャッシュ、ETag/Last-Modified対応
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass
class CacheEntry:
    """キャッシュエントリ"""
    url: str
    canonical_url: str
    content: str = ""  # キャッシュ済みコンテンツ（304復元用）
    content_hash: str = ""
    etag: str = ""
    last_modified: str = ""
    cached_at: float = 0.0
    content_length: int = 0
    status_code: int = 0


class UrlCache:
    """
    URLフェッチ結果のインメモリキャッシュ。
    
    機能:
    - URL正規化（UTM/tracking除去、fragment除去）
    - TTLベースの有効期限
    - ETag/Last-Modified対応の条件付きGETヘッダー生成
    - 重複URL検出
    """

    # 除去するトラッキングパラメータ
    _TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "source", "fbclid", "gclid", "msclkid", "dclid",
        "mc_cid", "mc_eid", "yclid", "_ga", "vero_id",
    }

    def __init__(self, ttl_sec: float = 3600.0):
        """
        Args:
            ttl_sec: キャッシュ有効期限（秒）。デフォルト1時間。
        """
        self.ttl_sec = ttl_sec
        self._cache: Dict[str, CacheEntry] = {}
        self._stats = {
            "hits": 0,
            "misses": 0,
            "total_canonicalized": 0,
            "duplicates_avoided": 0,
        }

    def canonicalize(self, url: str) -> str:
        """
        URL正規化（重複排除用）。
        - トラッキングパラメータ除去
        - fragment除去
        - クエリパラメータ順序正規化
        - ホスト名小文字化
        - 末尾スラッシュ正規化
        """
        self._stats["total_canonicalized"] += 1

        parsed = urlparse(url)
        # トラッキングパラメータ除去
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k.lower() not in self._TRACKING_PARAMS
        }
        # ソートして正規化
        sorted_query = urlencode(sorted(filtered.items()), doseq=True)
        # 末尾スラッシュ正規化（ルート以外）
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"

        return urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),
            path,
            parsed.params,
            sorted_query,
            "",  # fragment除去
        ))

    def has(self, url: str) -> bool:
        """キャッシュにURLが存在し有効期限内か"""
        canonical = self.canonicalize(url)
        entry = self._cache.get(canonical)
        if entry and (time.time() - entry.cached_at) < self.ttl_sec:
            return True
        return False

    def get(self, url: str) -> Optional[CacheEntry]:
        """キャッシュからエントリを取得"""
        canonical = self.canonicalize(url)
        entry = self._cache.get(canonical)
        if entry and (time.time() - entry.cached_at) < self.ttl_sec:
            self._stats["hits"] += 1
            return entry
        self._stats["misses"] += 1
        return None

    def put(
        self,
        url: str,
        content: str = "",
        content_hash: str = "",
        etag: str = "",
        last_modified: str = "",
        content_length: int = 0,
        status_code: int = 200,
    ) -> None:
        """キャッシュにエントリを保存"""
        canonical = self.canonicalize(url)
        self._cache[canonical] = CacheEntry(
            url=url,
            canonical_url=canonical,
            content=content,
            content_hash=content_hash,
            etag=etag,
            last_modified=last_modified,
            cached_at=time.time(),
            content_length=content_length,
            status_code=status_code,
        )

    def get_conditional_headers(self, url: str) -> Dict[str, str]:
        """
        条件付きGETヘッダーを生成。
        キャッシュにETag/Last-Modifiedがあればそれを使う。
        """
        entry = self.get(url)
        headers = {}
        if entry:
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified
        return headers

    def is_duplicate(self, url: str, urls_seen: set) -> bool:
        """
        正規化URLで重複チェック。
        urls_seenにcanonical URLを追加しつつ重複判定。
        """
        canonical = self.canonicalize(url)
        if canonical in urls_seen:
            self._stats["duplicates_avoided"] += 1
            return True
        urls_seen.add(canonical)
        return False

    def get_stats(self) -> Dict:
        """キャッシュ統計"""
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "ttl_sec": self.ttl_sec,
        }
