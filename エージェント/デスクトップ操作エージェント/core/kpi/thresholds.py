# -*- coding: utf-8 -*-
"""
Thresholds - 品質ゲート閾値の管理

"上がってはいけない"指標の閾値を定義し、
KPIサマリーに対して違反検出を行う。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple


@dataclass(frozen=True)
class Thresholds:
    """
    品質ゲートの閾値設定。
    超えたらCI/運用で検知・アラートを発火。
    """
    # 上がってはいけない
    max_pixel_rate: float = 0.02        # 2%超えたら危険
    max_misclick_rate: float = 0.005    # 0.5%
    max_wrong_state_rate: float = 0.01  # 1%
    max_cb_fire_rate: float = 0.03      # 3%
    max_hitl_rate: float = 0.05         # 5%（HITL多すぎ＝UX悪化）
    
    # 下がってはいけない（成功率）
    min_task_success_rate: float = 0.95   # 95%
    min_step_success_rate: float = 0.90   # 90%
    min_action_success_rate: float = 0.90 # 90%


def check_quality(summary: Dict[str, Any], th: Thresholds) -> List[Tuple[str, float, float, str]]:
    """
    KPIサマリーを閾値と照合し、違反を検出。
    
    Args:
        summary: KPIAggregator.finalize()の出力
        th: 閾値設定
    
    Returns:
        違反リスト: [(metric_name, actual, threshold, direction), ...]
        directionは"upper"（上がってはいけない）または"lower"（下がってはいけない）
    """
    a = summary.get("actions", {})
    tasks = summary.get("tasks", {})
    steps = summary.get("steps", {})
    
    violations = []
    
    def check_upper(name: str, actual: float, limit: float):
        """上がってはいけない指標のチェック"""
        if actual > limit:
            violations.append((name, actual, limit, "upper"))
    
    def check_lower(name: str, actual: float, limit: float):
        """下がってはいけない指標のチェック"""
        if actual < limit:
            violations.append((name, actual, limit, "lower"))
    
    # 上がってはいけない指標
    check_upper("pixel_rate", float(a.get("pixel_rate", 0.0)), th.max_pixel_rate)
    check_upper("misclick_rate", float(a.get("misclick_rate", 0.0)), th.max_misclick_rate)
    check_upper("wrong_state_rate", float(a.get("wrong_state_rate", 0.0)), th.max_wrong_state_rate)
    check_upper("cb_fire_rate", float(a.get("cb_fire_rate", 0.0)), th.max_cb_fire_rate)
    check_upper("hitl_rate", float(a.get("hitl_rate", 0.0)), th.max_hitl_rate)
    
    # 下がってはいけない指標
    check_lower("task_success_rate", float(tasks.get("success_rate", 1.0)), th.min_task_success_rate)
    check_lower("step_success_rate", float(steps.get("success_rate", 1.0)), th.min_step_success_rate)
    check_lower("action_success_rate", float(a.get("success_rate", 1.0)), th.min_action_success_rate)
    
    return violations


def format_violations(violations: List[Tuple[str, float, float, str]]) -> str:
    """違反を人間が読みやすい形式に整形"""
    if not violations:
        return "No quality gate violations."
    
    lines = ["=== QUALITY GATE VIOLATIONS ==="]
    for name, actual, limit, direction in violations:
        if direction == "upper":
            lines.append(f"  {name}: {actual:.6f} > {limit:.6f} (should be lower)")
        else:
            lines.append(f"  {name}: {actual:.6f} < {limit:.6f} (should be higher)")
    return "\n".join(lines)
