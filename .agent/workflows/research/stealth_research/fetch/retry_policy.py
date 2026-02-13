# -*- coding: utf-8 -*-
"""
リトライポリシー — CODEXAPP設計提案準拠
403/401/404/410は非リトライ。429/5xxのみリトライ。指数バックオフ+jitter。
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Optional

from ..config import RetryConfig


@dataclass
class RetryDecision:
    """リトライ判定結果（監査ログ用）"""
    should_retry: bool
    reason: str  # "retryable_status" | "non_retryable" | "max_attempts" | "timeout" | "connection_error"
    wait_sec: float = 0.0
    attempt_no: int = 0


class RetryPolicy:
    """リトライポリシー実装"""

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()

    def decide(
        self,
        status_code: Optional[int],
        attempt_no: int,
        error_class: Optional[str] = None,
        retry_after_header: Optional[str] = None,
    ) -> RetryDecision:
        """
        リトライすべきかどうかを判定。

        Args:
            status_code: HTTPステータスコード（Noneの場合は接続エラー）
            attempt_no: 現在の試行回数（1-indexed）
            error_class: エラー分類（"timeout", "connection_reset"等）
            retry_after_header: Retry-Afterヘッダー値
        """
        # 最大試行回数チェック
        if attempt_no >= self.config.max_attempts_per_url:
            return RetryDecision(
                should_retry=False,
                reason="max_attempts",
                attempt_no=attempt_no,
            )

        # 接続エラー（ステータスコードなし）
        if status_code is None:
            if error_class in ("timeout", "connection_reset", "dns_error"):
                wait = self._calc_backoff(attempt_no)
                return RetryDecision(
                    should_retry=True,
                    reason=f"connection_error:{error_class}",
                    wait_sec=wait,
                    attempt_no=attempt_no,
                )
            return RetryDecision(
                should_retry=False,
                reason=f"unknown_error:{error_class}",
                attempt_no=attempt_no,
            )

        # 非リトライ対象（403/401/404/410）→ 即座に諦め
        if status_code in self.config.non_retryable_statuses:
            return RetryDecision(
                should_retry=False,
                reason=f"non_retryable:{status_code}",
                attempt_no=attempt_no,
            )

        # リトライ対象（429/5xx）
        if status_code in self.config.retryable_statuses:
            wait = self._calc_backoff(attempt_no)
            # Retry-Afterヘッダーがあれば尊重
            if retry_after_header:
                try:
                    wait = max(wait, float(retry_after_header))
                except ValueError:
                    pass
            return RetryDecision(
                should_retry=True,
                reason=f"retryable_status:{status_code}",
                wait_sec=wait,
                attempt_no=attempt_no,
            )

        # 成功（2xx）やその他 → リトライ不要
        return RetryDecision(
            should_retry=False,
            reason=f"no_retry:{status_code}",
            attempt_no=attempt_no,
        )

    def _calc_backoff(self, attempt_no: int) -> float:
        """指数バックオフ + jitter"""
        base = self.config.backoff_base_sec * (2 ** (attempt_no - 1))
        capped = min(base, self.config.backoff_max_sec)
        jitter = capped * random.uniform(
            -self.config.jitter_range, self.config.jitter_range
        )
        return max(0.1, capped + jitter)

    async def wait_if_needed(self, decision: RetryDecision) -> None:
        """リトライ判定に基づいて待機"""
        if decision.should_retry and decision.wait_sec > 0:
            await asyncio.sleep(decision.wait_sec)
