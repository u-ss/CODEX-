# -*- coding: utf-8 -*-
"""
CircuitBreaker v2.0

連続失敗回数とEMAリスクを組み合わせたハイブリッドサーキットブレーカー。
GPT-5.2 相談結果に基づく実装。

使用例:
    from circuit_breaker import CircuitBreaker
    
    cb = CircuitBreaker()
    if cb.allow():
        try:
            result = perform_action()
            cb.record_success()
        except TimeoutError:
            cb.record_failure("timeout")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class CircuitState(Enum):
    """サーキットブレーカーの状態"""
    CLOSED = "closed"      # 正常：全ての操作を許可
    OPEN = "open"          # 遮断：全ての操作を拒否
    HALF_OPEN = "half_open"  # 試験：限定的に操作を許可


@dataclass(frozen=True)
class FailureRule:
    """失敗タイプごとの閾値・重み設定
    
    Attributes:
        threshold: 連続失敗でOPENになる回数
        weight: EMA計算時の重み（高いほどリスク寄与大）
        count_for_cb: False の場合は CB 対象外
    """
    threshold: int
    weight: float
    count_for_cb: bool = True


class CircuitBreaker:
    """連続失敗回数とEMAリスクを組み合わせたサーキットブレーカー
    
    OPEN条件（OR）:
        1. 連続失敗が閾値超え
        2. EMAリスク >= 0.9 かつ min_trials >= 10
        3. 重大失敗（access_denied等）は即OPEN
    
    状態遷移:
        CLOSED --[失敗]-> OPEN --[cooldown経過]-> HALF_OPEN
                                                    |
        CLOSED <--[成功]-- HALF_OPEN --[失敗]-> OPEN
    """

    # 失敗タイプ別パラメータ（GPT-5.2 確定済み）
    FAILURE_RULES: Dict[str, FailureRule] = {
        "timeout": FailureRule(threshold=5, weight=1.0),
        "notfound": FailureRule(threshold=3, weight=1.2),  # UI変更に敏感
        "stale": FailureRule(threshold=4, weight=0.8),     # リトライで回復しやすい
        "access_denied": FailureRule(threshold=1, weight=2.0),  # 1回で即OPEN
        "validation": FailureRule(threshold=0, weight=0.0, count_for_cb=False),  # CB対象外
    }

    def __init__(
        self,
        *,
        alpha: float = 0.2,
        open_risk_threshold: float = 0.9,
        min_trials: int = 10,
        cooldown_sec: int = 60,
        half_open_max_trials: int = 3,
    ) -> None:
        """CircuitBreaker を初期化
        
        Args:
            alpha: EMA の平滑化係数（0.2 = 直近5回くらいを重視）
            open_risk_threshold: OPEN になる EMA リスク閾値
            min_trials: EMA 判定を有効にする最小試行回数
            cooldown_sec: OPEN から HALF_OPEN への待機秒数
            half_open_max_trials: HALF_OPEN での最大試行回数
        """
        self.alpha = alpha
        self.open_risk_threshold = open_risk_threshold
        self.min_trials = min_trials
        self.cooldown_sec = cooldown_sec
        self.half_open_max_trials = half_open_max_trials

        self.state: CircuitState = CircuitState.CLOSED
        self.risk_ema: float = 0.0
        self.total_trials: int = 0

        # CB対象の失敗タイプのみ連続失敗カウンタを持つ
        self._consecutive_failures: Dict[str, int] = {
            key: 0 for key, rule in self.FAILURE_RULES.items() if rule.count_for_cb
        }

        self._opened_at: Optional[float] = None
        self._half_open_trials: int = 0

    def allow(self) -> bool:
        """実行許可判定を行う
        
        Returns:
            True: 操作を許可
            False: 操作を拒否（OPEN状態）
        """
        now = time.monotonic()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self._opened_at is None:
                self._opened_at = now
                return False

            # cooldown経過後にHALF_OPENへ遷移
            if (now - self._opened_at) >= self.cooldown_sec:
                self.state = CircuitState.HALF_OPEN
                self._half_open_trials = 0
            else:
                return False

        if self.state == CircuitState.HALF_OPEN:
            # HALF_OPENでは最大試行回数まで許可
            if self._half_open_trials < self.half_open_max_trials:
                self._half_open_trials += 1
                return True
            return False

        return False

    def record_success(self) -> None:
        """成功を記録する"""
        self.total_trials += 1
        self._update_ema(0.0)

        # 成功時は連続失敗を全リセット
        self._reset_consecutive_failures()

        # 要件: HALF_OPENで成功したらCLOSEDに戻る
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self._opened_at = None
            self._half_open_trials = 0

    def record_failure(self, failure_type: str) -> None:
        """失敗を記録する
        
        Args:
            failure_type: 失敗タイプ（timeout, notfound, stale, access_denied, validation）
        
        Raises:
            ValueError: サポートされていない failure_type の場合
        """
        rule = self.FAILURE_RULES.get(failure_type)
        if rule is None:
            raise ValueError(f"Unsupported failure_type: {failure_type}")

        # validation は CB 対象外（連続失敗にも EMA にも影響しない）
        if not rule.count_for_cb:
            return

        self.total_trials += 1
        self._update_ema(rule.weight)

        # 同種失敗のみ連続としてカウントし、他タイプは 0 へ
        for key in self._consecutive_failures:
            if key == failure_type:
                self._consecutive_failures[key] += 1
            else:
                self._consecutive_failures[key] = 0

        # 要件: HALF_OPEN 中に失敗したら OPEN に戻る
        if self.state == CircuitState.HALF_OPEN:
            self._open_circuit()
            return

        # 連続失敗閾値判定
        if self._consecutive_failures[failure_type] >= rule.threshold:
            self._open_circuit()
            return

        # EMA 判定: R_t >= 0.9 かつ min_trials >= 10
        if self.total_trials >= self.min_trials and self.risk_ema >= self.open_risk_threshold:
            self._open_circuit()

    def reset(self) -> None:
        """状態を初期化する"""
        self.state = CircuitState.CLOSED
        self.risk_ema = 0.0
        self.total_trials = 0
        self._opened_at = None
        self._half_open_trials = 0
        self._reset_consecutive_failures()

    def get_status(self) -> dict:
        """現在の状態を取得
        
        Returns:
            状態情報の辞書
        """
        return {
            "state": self.state.value,
            "risk_ema": round(self.risk_ema, 4),
            "total_trials": self.total_trials,
            "consecutive_failures": dict(self._consecutive_failures),
            "half_open_trials": self._half_open_trials,
        }

    def _update_ema(self, x_t: float) -> None:
        """EMA更新: R_t = α * x_t + (1-α) * R_{t-1}"""
        self.risk_ema = self.alpha * x_t + (1.0 - self.alpha) * self.risk_ema

    def _open_circuit(self) -> None:
        """サーキットをOPENにする"""
        self.state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_trials = 0

    def _reset_consecutive_failures(self) -> None:
        """全失敗タイプの連続失敗回数を 0 に戻す"""
        for key in self._consecutive_failures:
            self._consecutive_failures[key] = 0


# エクスポート
__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "FailureRule",
]
