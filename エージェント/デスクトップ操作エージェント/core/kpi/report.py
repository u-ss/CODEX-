# -*- coding: utf-8 -*-
"""
Report - KPIレポートCLI

traceファイルからKPIを集計し、JSON出力 + 品質ゲートチェックを行う。
CIパイプラインでの利用を想定。

使用例:
    python -m desktop_agent.core.kpi.report ./traces --out ./out/kpi_summary.json
"""
from __future__ import annotations
import os
import glob
import json
import sys
from typing import List, Dict, Any

from .trace_reader import read_jsonl
from .kpi_aggregator import KPIAggregator
from .thresholds import Thresholds, check_quality, format_violations


def collect_trace_files(paths: List[str]) -> List[str]:
    """
    パスリストからtraceファイルを収集。
    ディレクトリの場合は再帰的に.jsonlを探索。
    """
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += glob.glob(os.path.join(p, "**/*.jsonl"), recursive=True)
        elif os.path.isfile(p) and p.endswith(".jsonl"):
            files.append(p)
    return sorted(set(files))


def run(
    paths: List[str],
    out_json: str,
    fail_on_violation: bool = True,
    thresholds: Thresholds = Thresholds()
) -> int:
    """
    KPI集計を実行。
    
    Args:
        paths: traceファイル/ディレクトリのリスト
        out_json: 出力JSONパス
        fail_on_violation: 品質ゲート違反時にexit code 2を返すか
        thresholds: 閾値設定
    
    Returns:
        exit code (0=成功, 2=品質ゲート違反)
    """
    files = collect_trace_files(paths)
    agg = KPIAggregator()
    
    for fp in files:
        for ev in read_jsonl(fp):
            agg.process(ev)
    
    summary = agg.finalize()
    
    # JSON出力
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    # 品質ゲートチェック
    violations = check_quality(summary, thresholds)
    
    # コンソール出力
    print("=== KPI SUMMARY ===")
    print(f"tasks.success_rate={summary['tasks']['success_rate']:.3f} "
          f"avg_ms={summary['tasks']['avg_duration_ms']:.1f} "
          f"p90_ms={summary['tasks']['p90_duration_ms']:.1f}")
    print(f"steps.success_rate={summary['steps']['success_rate']:.3f} "
          f"avg_ms={summary['steps']['avg_duration_ms']:.1f} "
          f"p90_ms={summary['steps']['p90_duration_ms']:.1f}")
    print(f"actions.success_rate={summary['actions']['success_rate']:.3f} "
          f"pixel_rate={summary['actions']['pixel_rate']:.3f} "
          f"misclick_rate={summary['actions']['misclick_rate']:.3f} "
          f"wrong_state_rate={summary['actions']['wrong_state_rate']:.3f} "
          f"cb_fire_rate={summary['actions']['cb_fire_rate']:.3f} "
          f"hitl_rate={summary['actions']['hitl_rate']:.3f}")
    
    if violations:
        print(format_violations(violations), file=sys.stderr)
        return 2 if fail_on_violation else 0
    
    print("Quality gates: PASSED")
    return 0


if __name__ == "__main__":
    import argparse
    
    ap = argparse.ArgumentParser(description="KPI Report Generator")
    ap.add_argument("paths", nargs="+", help="trace jsonl file or directory")
    ap.add_argument("--out", required=True, help="output summary json path")
    ap.add_argument("--no-fail", action="store_true", help="do not fail on threshold violations")
    args = ap.parse_args()
    
    code = run(args.paths, args.out, fail_on_violation=(not args.no_fail))
    raise SystemExit(code)
