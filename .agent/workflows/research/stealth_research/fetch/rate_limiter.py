# -*- coding: utf-8 -*-
"""
レート制御 — Host別最小間隔・同時実行制限・Retry-After尊重
v2.0: asyncからsync版に変換、ログ証跡返却を追加
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Optional

from ..config import RateLimitConfig


@dataclass
class RateLimitResult:
    """レート制御の結果（監査証跡用）"""
    host: str
    waited_sec: float = 0.0
    retry_after_respected: bool = False
    retry_after_value: float = 0.0
    concurrent_slots_used: int = 0
    concurrent_slots_max: int = 0


class RateLimiter:
    """Host別レート制御（同期版）"""

    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._last_request: Dict[str, float] = defaultdict(float)
        self._host_locks: Dict[str, threading.Semaphore] = {}
        self._global_lock = threading.Semaphore(self.config.max_concurrent_global)
        self._retry_after: Dict[str, float] = {}  # host → 解除時刻
        self._lock = threading.Lock()
        # 統計
        self._total_waits: int = 0
        self._total_wait_sec: float = 0.0

    def _get_host_lock(self, host: str) -> threading.Semaphore:
        """Host別セマフォを取得（lazy init）"""
        with self._lock:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Semaphore(
                    self.config.max_concurrent_per_host
                )
            return self._host_locks[host]

    def acquire(self, host: str) -> RateLimitResult:
        """
        レート制御を取得（同期版）。必要な待機を行ってからリクエスト開始。

        Returns:
            RateLimitResult: 監査証跡（待機時間、Retry-After尊重等）
        """
        waited = 0.0
        retry_after_respected = False
        retry_after_value = 0.0

        # グローバル同時実行制限
        self._global_lock.acquire()

        # Host別同時実行制限
        host_lock = self._get_host_lock(host)
        host_lock.acquire()

        # Retry-After チェック
        if self.config.respect_retry_after and host in self._retry_after:
            retry_until = self._retry_after[host]
            now = time.time()
            if now < retry_until:
                wait_sec = retry_until - now
                time.sleep(wait_sec)
                waited += wait_sec
                retry_after_respected = True
                retry_after_value = wait_sec
            with self._lock:
                if host in self._retry_after:
                    del self._retry_after[host]

        # 最小間隔チェック
        now = time.time()
        with self._lock:
            last = self._last_request.get(host, 0.0)
        elapsed = now - last
        if elapsed < self.config.min_interval_sec:
            wait_sec = self.config.min_interval_sec - elapsed
            time.sleep(wait_sec)
            waited += wait_sec

        with self._lock:
            self._last_request[host] = time.time()
            if waited > 0:
                self._total_waits += 1
                self._total_wait_sec += waited

        return RateLimitResult(
            host=host,
            waited_sec=waited,
            retry_after_respected=retry_after_respected,
            retry_after_value=retry_after_value,
            concurrent_slots_used=self.config.max_concurrent_per_host - host_lock._value,
            concurrent_slots_max=self.config.max_concurrent_per_host,
        )

    def release(self, host: str) -> None:
        """レート制御を解放"""
        if host in self._host_locks:
            self._host_locks[host].release()
        self._global_lock.release()

    def set_retry_after(self, host: str, seconds: float) -> None:
        """Retry-Afterヘッダーを記録"""
        with self._lock:
            self._retry_after[host] = time.time() + seconds

    def get_stats(self) -> Dict:
        """統計情報（監査用）"""
        with self._lock:
            return {
                "total_waits": self._total_waits,
                "total_wait_sec": round(self._total_wait_sec, 2),
                "last_request_times": dict(self._last_request),
                "retry_after_hosts": {
                    h: max(0, t - time.time())
                    for h, t in self._retry_after.items()
                },
            }
