# -*- coding: utf-8 -*-
"""
Trend - 移動平均・EWMAによるトレンド分析

KPI時系列に対して:
- 移動平均（ノイズ除去）
- EWMA（直近変化の早期検出）
- 標準偏差（異常検知用）
を計算する。
"""
from __future__ import annotations
from typing import List, Optional
import math


def rolling_mean(xs: List[float], window: int) -> List[Optional[float]]:
    """
    単純移動平均を計算。
    
    Args:
        xs: 時系列データ
        window: ウィンドウサイズ
    
    Returns:
        移動平均のリスト（先頭はNone）
    """
    out: List[Optional[float]] = [None] * len(xs)
    if window <= 0:
        return out
    
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= window:
            s -= xs[i - window]
        if i >= window - 1:
            out[i] = s / window
    return out


def ewma(xs: List[float], alpha: float) -> List[Optional[float]]:
    """
    指数加重移動平均（EWMA）を計算。
    
    Args:
        xs: 時系列データ
        alpha: 平滑化係数（0.1-0.3が一般的）
    
    Returns:
        EWMAのリスト
    """
    out: List[Optional[float]] = [None] * len(xs)
    if not xs:
        return out
    
    m = xs[0]
    out[0] = m
    for i in range(1, len(xs)):
        m = alpha * xs[i] + (1 - alpha) * m
        out[i] = m
    return out


def rolling_std(xs: List[float], window: int) -> List[Optional[float]]:
    """
    移動標準偏差を計算。
    
    Args:
        xs: 時系列データ
        window: ウィンドウサイズ
    
    Returns:
        標準偏差のリスト（先頭はNone）
    """
    out: List[Optional[float]] = [None] * len(xs)
    if window <= 1:
        return out
    
    for i in range(len(xs)):
        if i < window - 1:
            continue
        w = xs[i - window + 1 : i + 1]
        mu = sum(w) / window
        var = sum((a - mu) ** 2 for a in w) / (window - 1)
        out[i] = math.sqrt(var)
    return out


def detect_trend(xs: List[float], window: int = 10) -> str:
    """
    トレンドの方向を検出。
    
    Args:
        xs: 時系列データ
        window: 比較ウィンドウ
    
    Returns:
        "up", "down", "stable" のいずれか
    """
    if len(xs) < window * 2:
        return "stable"
    
    recent = sum(xs[-window:]) / window
    previous = sum(xs[-window*2:-window]) / window
    
    if previous == 0:
        return "stable"
    
    change_rate = (recent - previous) / abs(previous)
    
    if change_rate > 0.1:
        return "up"
    elif change_rate < -0.1:
        return "down"
    return "stable"
