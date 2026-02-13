# -*- coding: utf-8 -*-
"""
Alert Engine - KPIアラート統合エンジン

トレンド分析と閾値検知を統合し、
異常を検出してアラートを発火する。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import math

from .trend import ewma, rolling_std
from .history import get_metric_series


@dataclass(frozen=True)
class MetricSpec:
    """メトリクス仕様"""
    name: str           # ドットパス（例: "actions.pixel_rate"）
    direction: str      # "upper"（上がってはいけない）or "lower"（下がってはいけない）
    method: str         # "quantile" or "ewma"


@dataclass(frozen=True)
class Alert:
    """アラートイベント"""
    metric: str
    kind: str           # "threshold" or "ewma"
    ts: float
    value: float
    threshold: Optional[float]
    severity: str       # "warn" / "crit"
    detail: str


def quantile(xs: List[float], q: float) -> Optional[float]:
    """分位点を計算"""
    if not xs:
        return None
    ys = sorted(xs)
    k = max(0, min(len(ys) - 1, int(math.ceil(q * len(ys))) - 1))
    return float(ys[k])


@dataclass(frozen=True)
class AutoThresholdConfig:
    """自動閾値設定"""
    warmup_n: int = 30          # 最低30点集まるまでは閾値固定推奨
    lookback_n: int = 200       # 直近200点で基準を作る
    upper_q: float = 0.98       # "上がってはいけない"はp98
    lower_q: float = 0.02       # "下がってはいけない"はp02


@dataclass(frozen=True)
class EWMAChartConfig:
    """EWMA管理図設定"""
    alpha: float = 0.2        # 0.1-0.3くらいで運用
    sigma_window: int = 50    # σ推定の窓
    z_limit: float = 3.0      # 3σ相当


def ewma_anomalies(xs: List[float], cfg: EWMAChartConfig) -> List[tuple]:
    """
    EWMA管理図による異常点検出。
    
    Returns:
        [(index, value, z_score), ...] 異常点のリスト
    """
    out = []
    m = ewma(xs, cfg.alpha)
    s = rolling_std(xs, cfg.sigma_window)
    
    for i, x in enumerate(xs):
        if m[i] is None or s[i] is None or s[i] == 0:
            continue
        z = (x - m[i]) / s[i]
        if abs(z) >= cfg.z_limit:
            out.append((i, x, z))
    return out


class AlertEngine:
    """
    アラートエンジン本体。
    履歴データに対して複数メトリクスの異常検知を行う。
    """
    
    def __init__(
        self,
        specs: List[MetricSpec],
        th_cfg: AutoThresholdConfig = AutoThresholdConfig(),
        ewma_cfg: EWMAChartConfig = EWMAChartConfig()
    ):
        self.specs = specs
        self.th_cfg = th_cfg
        self.ewma_cfg = ewma_cfg
    
    def detect(self, history: List[Dict[str, Any]]) -> List[Alert]:
        """
        履歴データから異常を検出。
        
        Args:
            history: [{ts:..., summary:...}, ...] ts昇順
        
        Returns:
            アラートのリスト
        """
        alerts: List[Alert] = []
        n = len(history)
        if n == 0:
            return alerts
        
        for spec in self.specs:
            xs = get_metric_series(history, spec.name)
            ts_last = float(history[-1]["ts"])
            x_last = float(xs[-1]) if xs else 0.0
            
            # 分位ベース閾値
            if spec.method == "quantile":
                if n < self.th_cfg.warmup_n:
                    continue
                look = xs[-self.th_cfg.lookback_n:] if n > self.th_cfg.lookback_n else xs
                
                if spec.direction == "upper":
                    th = quantile(look, self.th_cfg.upper_q)
                    if th is not None and x_last > th:
                        alerts.append(Alert(
                            spec.name, "threshold", ts_last, x_last, th,
                            severity="warn",
                            detail=f"value {x_last:.6f} > p{int(self.th_cfg.upper_q*100)} {th:.6f}"
                        ))
                else:
                    th = quantile(look, self.th_cfg.lower_q)
                    if th is not None and x_last < th:
                        alerts.append(Alert(
                            spec.name, "threshold", ts_last, x_last, th,
                            severity="warn",
                            detail=f"value {x_last:.6f} < p{int(self.th_cfg.lower_q*100)} {th:.6f}"
                        ))
            
            # EWMA異常検知
            if spec.method == "ewma":
                anoms = ewma_anomalies(xs, self.ewma_cfg)
                # 直近だけ見る（最後の点が異常ならアラート）
                if anoms and anoms[-1][0] == n - 1:
                    _, _, z = anoms[-1]
                    # direction を片側化
                    if spec.direction == "upper" and z > 0:
                        sev = "crit" if abs(z) >= self.ewma_cfg.z_limit + 1 else "warn"
                        alerts.append(Alert(
                            spec.name, "ewma", ts_last, x_last, None, sev,
                            detail=f"ewma z={z:.2f}"
                        ))
                    if spec.direction == "lower" and z < 0:
                        sev = "crit" if abs(z) >= self.ewma_cfg.z_limit + 1 else "warn"
                        alerts.append(Alert(
                            spec.name, "ewma", ts_last, x_last, None, sev,
                            detail=f"ewma z={z:.2f}"
                        ))
        
        return alerts


# デフォルトのメトリクス仕様（運用で最初に監視すべきもの）
DEFAULT_METRIC_SPECS = [
    MetricSpec("actions.pixel_rate", "upper", "quantile"),
    MetricSpec("actions.misclick_rate", "upper", "quantile"),
    MetricSpec("actions.wrong_state_rate", "upper", "quantile"),
    MetricSpec("actions.cb_fire_rate", "upper", "quantile"),
    MetricSpec("actions.hitl_rate", "upper", "quantile"),
    MetricSpec("tasks.success_rate", "lower", "quantile"),
    MetricSpec("steps.success_rate", "lower", "quantile"),
    MetricSpec("actions.success_rate", "lower", "quantile"),
]
