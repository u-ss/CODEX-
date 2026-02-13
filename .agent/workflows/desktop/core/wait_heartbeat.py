# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - Heartbeat Wait
進捗がある限り延長、詰まり検出
"""

from dataclasses import dataclass
from typing import Callable, Optional, TypeVar
import time


@dataclass
class ProgressHeartbeat:
    """進捗ハートビート"""
    last_progress_ms: int = 0
    last_value: Optional[str] = None
    stable_since_ms: int = 0
    samples: int = 0


@dataclass(frozen=True)
class HeartbeatWaitConfig:
    """ハートビート待機設定"""
    poll_ms_fast: int = 200
    poll_ms_slow: int = 800
    slow_after_ms: int = 5000
    stall_ms: int = 15000           # 進捗停止判定
    stable_ms: int = 1200           # 安定判定
    max_total_ms: int = 180000      # 最大待機


@dataclass
class HeartbeatResult:
    """待機結果"""
    final_value: Optional[str] = None
    is_done: bool = False
    is_stalled: bool = False
    is_timeout: bool = False
    samples_taken: int = 0
    elapsed_ms: int = 0


T = TypeVar('T')


def heartbeat_wait(
    *,
    sample_fn: Callable[[], Optional[str]],
    is_done_fn: Callable[[Optional[str]], bool],
    cfg: HeartbeatWaitConfig,
) -> HeartbeatResult:
    """
    ハートビート待機
    
    Args:
        sample_fn: サンプル取得関数（text/hash/len等を返す）
        is_done_fn: 完了判定関数
        cfg: 設定
    
    Returns:
        HeartbeatResult
    """
    start_ms = int(time.time() * 1000)
    hb = ProgressHeartbeat(last_progress_ms=start_ms)
    
    while True:
        now_ms = int(time.time() * 1000)
        elapsed_ms = now_ms - start_ms
        
        # 最大タイムアウト
        if elapsed_ms > cfg.max_total_ms:
            return HeartbeatResult(
                final_value=hb.last_value,
                is_timeout=True,
                samples_taken=hb.samples,
                elapsed_ms=elapsed_ms,
            )
        
        # サンプル取得
        try:
            value = sample_fn()
        except Exception:
            value = None
        
        hb.samples += 1
        
        # 完了判定
        if is_done_fn(value):
            return HeartbeatResult(
                final_value=value,
                is_done=True,
                samples_taken=hb.samples,
                elapsed_ms=elapsed_ms,
            )
        
        # 進捗判定
        has_progress = value is not None and value != hb.last_value
        
        if has_progress:
            hb.last_value = value
            hb.last_progress_ms = now_ms
            hb.stable_since_ms = 0
        else:
            # 変化なし
            if hb.stable_since_ms == 0:
                hb.stable_since_ms = now_ms
            
            # 安定化判定
            stable_duration = now_ms - hb.stable_since_ms
            if stable_duration >= cfg.stable_ms:
                return HeartbeatResult(
                    final_value=hb.last_value,
                    is_done=True,
                    samples_taken=hb.samples,
                    elapsed_ms=elapsed_ms,
                )
        
        # 詰まり判定
        stall_duration = now_ms - hb.last_progress_ms
        if stall_duration > cfg.stall_ms:
            return HeartbeatResult(
                final_value=hb.last_value,
                is_stalled=True,
                samples_taken=hb.samples,
                elapsed_ms=elapsed_ms,
            )
        
        # ポーリング間隔決定
        poll_ms = cfg.poll_ms_slow if elapsed_ms > cfg.slow_after_ms else cfg.poll_ms_fast
        time.sleep(poll_ms / 1000)


def wrap_length_sample(text_fn: Callable[[], Optional[str]]) -> Callable[[], Optional[str]]:
    """テキスト長をサンプル値として返すラッパー"""
    def sample() -> Optional[str]:
        text = text_fn()
        if text is None:
            return None
        return str(len(text))
    return sample


def wrap_hash_sample(
    text_fn: Callable[[], Optional[str]],
    hash_fn: Callable[[str], str],
) -> Callable[[], Optional[str]]:
    """テキストハッシュをサンプル値として返すラッパー"""
    def sample() -> Optional[str]:
        text = text_fn()
        if text is None:
            return None
        return hash_fn(text)
    return sample
