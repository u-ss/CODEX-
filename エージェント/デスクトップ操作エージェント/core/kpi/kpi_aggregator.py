# -*- coding: utf-8 -*-
"""
KPI Aggregator - TraceEventからKPIを集計

タスク/ステップ/アクション単位で以下を算出:
- 成功率
- 平均所要時間、p50/p90
- リトライ率、CB発火率、HITL発生率
- 上がってはいけない指標: Pixel使用率、MISCLICK率、WRONG_STATE率
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any
import math


@dataclass
class ActionStats:
    """アクション統計"""
    count: int = 0
    ok_count: int = 0
    total_duration_ms: float = 0.0
    retry_actions: int = 0
    cb_fires: int = 0
    hitl: int = 0
    pixel: int = 0
    misclick: int = 0
    wrong_state: int = 0
    fail_type_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class StepStats:
    """ステップ統計"""
    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    ok: Optional[bool] = None
    actions: ActionStats = field(default_factory=ActionStats)


@dataclass
class TaskStats:
    """タスク統計"""
    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    ok: Optional[bool] = None
    steps: Dict[str, StepStats] = field(default_factory=dict)
    actions: ActionStats = field(default_factory=ActionStats)


def _inc(d: Dict[str, int], k: Optional[str], n: int = 1):
    """辞書のカウンタをインクリメント"""
    if not k:
        return
    d[k] = d.get(k, 0) + n


class KPIAggregator:
    """
    TraceEventを順に食わせるだけでKPIが作られる。
    
    使用例:
        agg = KPIAggregator()
        for ev in read_jsonl("trace.jsonl"):
            agg.process(ev)
        summary = agg.finalize()
    """
    
    def __init__(self) -> None:
        self.tasks: Dict[str, TaskStats] = {}
        
        # 全体集計
        self.total_actions = ActionStats()
        self.total_steps_ok = 0
        self.total_steps = 0
        self.total_tasks_ok = 0
        self.total_tasks = 0
        
        # 分位（p50/p90）を出したい場合に備えてdurationを貯める
        self._action_durations: List[float] = []
        self._task_durations: List[float] = []
        self._step_durations: List[float] = []
    
    def _get_task(self, task_id: str) -> TaskStats:
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskStats()
        return self.tasks[task_id]
    
    def _get_step(self, t: TaskStats, step_id: str) -> StepStats:
        if step_id not in t.steps:
            t.steps[step_id] = StepStats()
        return t.steps[step_id]
    
    def process(self, ev) -> None:
        """TraceEventを処理してKPIに反映"""
        t = self._get_task(ev.task_id)
        
        if ev.event == "task_start":
            t.start_ts = t.start_ts or ev.ts
            return
        if ev.event == "task_end":
            t.end_ts = ev.ts
            t.ok = bool(ev.ok) if ev.ok is not None else t.ok
            return
        if ev.event == "step_start":
            if ev.step_id:
                s = self._get_step(t, str(ev.step_id))
                s.start_ts = s.start_ts or ev.ts
            return
        if ev.event == "step_end":
            if ev.step_id:
                s = self._get_step(t, str(ev.step_id))
                s.end_ts = ev.ts
                if ev.ok is not None:
                    s.ok = bool(ev.ok)
            return
        
        # KPIに効くのは基本action_end
        if ev.event != "action_end":
            return
        
        # action集計（task/step/totalの3箇所に加算）
        self._acc_action(self.total_actions, ev)
        self._acc_action(t.actions, ev)
        
        if ev.step_id:
            s = self._get_step(t, str(ev.step_id))
            self._acc_action(s.actions, ev)
        
        # duration蓄積（分位用）
        if ev.duration_ms is not None:
            self._action_durations.append(float(ev.duration_ms))
    
    def _acc_action(self, st: ActionStats, ev) -> None:
        """アクション統計に加算"""
        st.count += 1
        if ev.ok is True:
            st.ok_count += 1
        if ev.duration_ms is not None:
            st.total_duration_ms += float(ev.duration_ms)
        
        # retry: retry_index>=1を「リトライされたアクション」と数える
        if ev.retry_index is not None and ev.retry_index >= 1:
            st.retry_actions += 1
        
        if ev.cb_fired:
            st.cb_fires += 1
        if ev.hitl:
            st.hitl += 1
        
        if ev.layer and str(ev.layer).lower() == "pixel":
            st.pixel += 1
        
        # "上がってはいけない"代表例：FailTypeで拾う
        if ev.fail_type:
            ft = str(ev.fail_type)
            _inc(st.fail_type_counts, ft, 1)
            if ft == "MISCLICK":
                st.misclick += 1
            if ft == "WRONG_STATE":
                st.wrong_state += 1
    
    @staticmethod
    def _rate(num: int, den: int) -> float:
        if den <= 0:
            return 0.0
        return num / den
    
    @staticmethod
    def _mean(total: float, n: int) -> float:
        if n <= 0:
            return 0.0
        return total / n
    
    @staticmethod
    def _percentile(xs: List[float], p: float) -> float:
        if not xs:
            return 0.0
        ys = sorted(xs)
        # nearest-rank
        k = max(0, min(len(ys) - 1, int(math.ceil(p * len(ys))) - 1))
        return float(ys[k])
    
    def finalize(self) -> Dict[str, Any]:
        """全イベント処理後にKPIサマリーを生成"""
        # task/stepの成功率と時間計算
        self.total_tasks = 0
        self.total_tasks_ok = 0
        self.total_steps = 0
        self.total_steps_ok = 0
        
        for task_id, t in self.tasks.items():
            if t.start_ts is not None and t.end_ts is not None:
                self.total_tasks += 1
                dur_ms = (t.end_ts - t.start_ts) * 1000.0
                self._task_durations.append(dur_ms)
                if t.ok is True:
                    self.total_tasks_ok += 1
            
            for step_id, s in t.steps.items():
                if s.start_ts is not None and s.end_ts is not None:
                    self.total_steps += 1
                    dur_ms = (s.end_ts - s.start_ts) * 1000.0
                    self._step_durations.append(dur_ms)
                    if s.ok is True:
                        self.total_steps_ok += 1
        
        a = self.total_actions
        out = {
            "tasks": {
                "count": self.total_tasks,
                "success_rate": self._rate(self.total_tasks_ok, self.total_tasks),
                "avg_duration_ms": self._mean(sum(self._task_durations), len(self._task_durations)),
                "p50_duration_ms": self._percentile(self._task_durations, 0.50),
                "p90_duration_ms": self._percentile(self._task_durations, 0.90),
            },
            "steps": {
                "count": self.total_steps,
                "success_rate": self._rate(self.total_steps_ok, self.total_steps),
                "avg_duration_ms": self._mean(sum(self._step_durations), len(self._step_durations)),
                "p50_duration_ms": self._percentile(self._step_durations, 0.50),
                "p90_duration_ms": self._percentile(self._step_durations, 0.90),
            },
            "actions": {
                "count": a.count,
                "success_rate": self._rate(a.ok_count, a.count),
                "avg_duration_ms": self._mean(a.total_duration_ms, a.count),
                "p50_duration_ms": self._percentile(self._action_durations, 0.50),
                "p90_duration_ms": self._percentile(self._action_durations, 0.90),
                "retry_rate": self._rate(a.retry_actions, a.count),
                "cb_fire_rate": self._rate(a.cb_fires, a.count),
                "hitl_rate": self._rate(a.hitl, a.count),
                "pixel_rate": self._rate(a.pixel, a.count),
                "misclick_rate": self._rate(a.misclick, a.count),
                "wrong_state_rate": self._rate(a.wrong_state, a.count),
                "fail_type_counts": dict(sorted(a.fail_type_counts.items(), key=lambda x: -x[1])),
            },
        }
        return out
