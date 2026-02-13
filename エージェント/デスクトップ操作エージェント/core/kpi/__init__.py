# -*- coding: utf-8 -*-
"""
Desktop Agent KPI モジュール

Phase 2で実装されたKPI集計・トレンド検知・アラートエンジン群。
traceデータから運用KPIを自動集計し、品質ゲートを実現する。
"""

from .trace_reader import read_jsonl, normalize_event, TraceEvent
from .kpi_aggregator import KPIAggregator, ActionStats, StepStats, TaskStats
from .thresholds import Thresholds, check_quality
from .history import KPIHistoryRecord, append_history, now_ts
from .trend import rolling_mean, ewma, rolling_std
from .alert_engine import AlertEngine, Alert, MetricSpec

__all__ = [
    # trace_reader
    "read_jsonl",
    "normalize_event",
    "TraceEvent",
    # kpi_aggregator
    "KPIAggregator",
    "ActionStats",
    "StepStats",
    "TaskStats",
    # thresholds
    "Thresholds",
    "check_quality",
    # history
    "KPIHistoryRecord",
    "append_history",
    "now_ts",
    # trend
    "rolling_mean",
    "ewma",
    "rolling_std",
    # alert_engine
    "AlertEngine",
    "Alert",
    "MetricSpec",
]
