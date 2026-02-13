# -*- coding: utf-8 -*-
"""
HTTPフェッチャー — requests/httpx主体、CAPTCHA検知→停止
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests as req

from ..config import FetchConfig


@dataclass
class FetchResult:
    """フェッチ結果"""
    url: str
    final_url: str = ""
    status_code: Optional[int] = None
    content: str = ""
    content_type: str = ""
    error_class: str = ""  # "timeout" | "connection_reset" | "dns_error" | ""
    blocked_signal: str = ""  # "captcha" | "forbidden" | "rate_limited" | ""
    duration_ms: float = 0.0
    headers: dict = None

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}

    @property
    def success(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300

    @property
    def host(self) -> str:
        try:
            return urlparse(self.url).netloc.lower()
        except Exception:
            return "unknown"


# CAPTCHA/チャレンジ検知パターン
_CAPTCHA_SIGNALS = [
    "captcha", "challenge", "turnstile", "recaptcha",
    "hcaptcha", "cf-browser-verification", "just a moment",
    "checking your browser", "access denied", "please verify",
]


class HttpFetcher:
    """HTTP フェッチャー（requests ベース）"""

    def __init__(self, config: Optional[FetchConfig] = None):
        self.config = config or FetchConfig()
        self._session = req.Session()

    def fetch(self, url: str, extra_headers: dict = None) -> FetchResult:
        """
        URLをHTTP GETで取得。

        CAPTCHA/ブロック検知付き。成功/失敗問わずFetchResultを返す。
        extra_headers: 追加ヘッダー（conditional GET用 ETag/Last-Modified等）
        """
        start = time.time()
        ua = random.choice(self.config.user_agents)

        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = self._session.get(
                url,
                timeout=self.config.timeout_sec,
                headers=headers,
                allow_redirects=True,
            )
            elapsed_ms = (time.time() - start) * 1000

            content = resp.text[:self.config.max_chars] if resp.text else ""
            blocked_signal = self._detect_block(resp.status_code, content)

            return FetchResult(
                url=url,
                final_url=str(resp.url),
                status_code=resp.status_code,
                content=content,
                content_type=resp.headers.get("Content-Type", ""),
                blocked_signal=blocked_signal,
                duration_ms=elapsed_ms,
                headers=dict(resp.headers),
            )

        except req.exceptions.Timeout:
            return FetchResult(
                url=url,
                error_class="timeout",
                duration_ms=(time.time() - start) * 1000,
            )
        except req.exceptions.ConnectionError as e:
            err = "dns_error" if "NameResolutionError" in str(e) else "connection_reset"
            return FetchResult(
                url=url,
                error_class=err,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return FetchResult(
                url=url,
                error_class=f"unknown:{type(e).__name__}",
                duration_ms=(time.time() - start) * 1000,
            )

    def _detect_block(self, status_code: int, content: str) -> str:
        """CAPTCHA/ブロック信号を検知（突破はしない、検知して記録するだけ）"""
        if status_code == 403:
            return "forbidden"
        if status_code == 429:
            return "rate_limited"
        if status_code == 401:
            return "unauthorized"

        # コンテンツベースのCAPTCHA検知
        lower = content.lower()
        for signal in _CAPTCHA_SIGNALS:
            if signal in lower:
                return "captcha"

        return ""

    def close(self):
        """セッションをクローズ"""
        self._session.close()
