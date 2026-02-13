# -*- coding: utf-8 -*-
"""
サーキットブレーカー — URL単位 + Host単位
CODEXAPP設計: 403/captcha >= 3 in 10min → open 5min
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..config import BreakerConfig


@dataclass
class BreakerState:
    """ブレーカーの状態"""
    failures: List[float] = field(default_factory=list)  # 失敗時刻リスト
    state: str = "closed"  # "closed" | "open" | "half_open"
    opened_at: float = 0.0


@dataclass
class BreakerDecision:
    """ブレーカー判定結果（監査ログ用）"""
    allowed: bool
    reason: str  # "closed" | "open" | "half_open_probe" | "url_exhausted"
    host: str
    url: str
    fail_count: int = 0
    cooldown_remaining_sec: float = 0.0


class CircuitBreaker:
    """サーキットブレーカー実装"""

    def __init__(self, config: Optional[BreakerConfig] = None):
        self.config = config or BreakerConfig()
        self._host_states: Dict[str, BreakerState] = defaultdict(BreakerState)
        self._url_attempts: Dict[str, int] = defaultdict(int)

    def check(self, url: str) -> BreakerDecision:
        """
        URLへのリクエストが許可されるか判定。

        Returns:
            BreakerDecision: allowed=True なら実行可、False なら停止
        """
        host = self._extract_host(url)
        now = time.time()

        # URL単位チェック
        if self._url_attempts[url] >= self.config.url_max_attempts:
            return BreakerDecision(
                allowed=False,
                reason="url_exhausted",
                host=host,
                url=url,
                fail_count=self._url_attempts[url],
            )

        # Host単位チェック
        state = self._host_states[host]

        if state.state == "open":
            elapsed = now - state.opened_at
            if elapsed >= self.config.host_cooldown_sec:
                # クールダウン終了 → half_open
                state.state = "half_open"
                return BreakerDecision(
                    allowed=True,
                    reason="half_open_probe",
                    host=host,
                    url=url,
                    fail_count=len(state.failures),
                )
            else:
                remaining = self.config.host_cooldown_sec - elapsed
                return BreakerDecision(
                    allowed=False,
                    reason="open",
                    host=host,
                    url=url,
                    fail_count=len(state.failures),
                    cooldown_remaining_sec=remaining,
                )

        return BreakerDecision(
            allowed=True,
            reason=state.state,
            host=host,
            url=url,
            fail_count=len(state.failures),
        )

    def record_success(self, url: str) -> None:
        """成功を記録 — half_openならclosedに戻す"""
        host = self._extract_host(url)
        state = self._host_states[host]
        if state.state == "half_open":
            state.state = "closed"
            state.failures.clear()

    def record_failure(self, url: str, blocked_reason: str = "") -> None:
        """
        失敗を記録 — 閾値超えたらopen。

        Args:
            url: 失敗したURL
            blocked_reason: "403" | "captcha" | "rate_limited" 等
        """
        host = self._extract_host(url)
        now = time.time()

        # URL単位カウント
        self._url_attempts[url] += 1

        # Host単位: ウィンドウ内の失敗を記録
        state = self._host_states[host]
        state.failures.append(now)

        # ウィンドウ外の古い失敗を除去
        cutoff = now - self.config.window_sec
        state.failures = [t for t in state.failures if t > cutoff]

        # 閾値チェック
        if len(state.failures) >= self.config.host_fail_threshold:
            state.state = "open"
            state.opened_at = now

        # half_openで失敗したら再度open
        if state.state == "half_open":
            state.state = "open"
            state.opened_at = now

    def get_stats(self) -> Dict:
        """統計情報（監査用）"""
        now = time.time()
        stats = {
            "hosts": {},
            "url_attempts": dict(self._url_attempts),
        }
        for host, state in self._host_states.items():
            recent = [t for t in state.failures if t > now - self.config.window_sec]
            stats["hosts"][host] = {
                "state": state.state,
                "recent_failures": len(recent),
                "total_recorded": len(state.failures),
            }
        return stats

    @staticmethod
    def _extract_host(url: str) -> str:
        """URLからホスト名を抽出"""
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return "unknown"
