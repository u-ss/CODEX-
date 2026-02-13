# -*- coding: utf-8 -*-
"""
ベンチマーク基盤 — 固定クエリセットでの回帰テスト
v3.0: ゴールデン期待値、品質閾値チェック、スコアリング
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class BenchmarkCase:
    """ベンチマークケース"""
    query: str
    expected_min_results: int = 3
    expected_domains: List[str] = field(default_factory=list)
    min_extraction_ratio: float = 0.05
    max_empty_ratio: float = 0.5


@dataclass
class BenchmarkResult:
    """ベンチマーク結果"""
    case: str
    passed: bool
    score: float  # 0-100
    details: Dict = field(default_factory=dict)
    duration_sec: float = 0.0


# デフォルトベンチマークスイート
DEFAULT_BENCHMARK_SUITE = [
    BenchmarkCase(
        query="Python web scraping best practices",
        expected_min_results=3,
        min_extraction_ratio=0.05,
        max_empty_ratio=0.5,
    ),
    BenchmarkCase(
        query="AI agent frameworks comparison 2026",
        expected_min_results=3,
        min_extraction_ratio=0.05,
        max_empty_ratio=0.5,
    ),
    BenchmarkCase(
        query="machine learning deployment strategies",
        expected_min_results=3,
        min_extraction_ratio=0.05,
        max_empty_ratio=0.3,
    ),
]


class Benchmark:
    """
    ベンチマーク実行エンジン。

    固定クエリセットでOrchestratorを実行し、品質閾値をチェック。
    回帰テストとして使用可能。
    """

    def __init__(self, suite: List[BenchmarkCase] = None):
        self.suite = suite or DEFAULT_BENCHMARK_SUITE

    def run(self, orchestrator, output_dir: str = None) -> Dict:
        """
        ベンチマーク全ケースを実行。

        Args:
            orchestrator: Orchestratorインスタンス
            output_dir: 出力先

        Returns:
            ベンチマーク結果辞書
        """
        results: List[BenchmarkResult] = []
        total_score = 0.0
        start_all = time.time()

        for case in self.suite:
            start_case = time.time()
            try:
                res = orchestrator.run(
                    queries=[case.query],
                    output_dir=output_dir,
                )
                summary = res.summary
                result = self._evaluate(case, summary)
                result.duration_sec = round(time.time() - start_case, 2)
            except Exception as e:
                result = BenchmarkResult(
                    case=case.query,
                    passed=False,
                    score=0.0,
                    details={"error": str(e)},
                    duration_sec=round(time.time() - start_case, 2),
                )
            results.append(result)
            total_score += result.score

        overall = {
            "total_cases": len(self.suite),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "avg_score": round(total_score / len(self.suite), 1) if self.suite else 0,
            "total_duration_sec": round(time.time() - start_all, 2),
            "results": [asdict(r) for r in results],
        }

        # ファイルに保存
        if output_dir:
            out_path = Path(output_dir) / "benchmark_results.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(overall, f, indent=2, ensure_ascii=False)

        return overall

    def _evaluate(self, case: BenchmarkCase, summary: Dict) -> BenchmarkResult:
        """ケース結果を評価してスコアリング"""
        score = 0.0
        details = {}

        # 1. 成功率 (30点)
        total = summary.get("total_fetches", 0)
        success = summary.get("successful_fetches", 0)
        success_rate = success / total if total > 0 else 0
        score_success = min(30.0, success_rate * 30.0)
        score += score_success
        details["success_rate"] = round(success_rate, 3)

        # 2. 抽出品質 (30点)
        ext_ratio = summary.get("avg_extraction_ratio", 0)
        if ext_ratio >= 0.15:
            score_ext = 30.0
        elif ext_ratio >= 0.10:
            score_ext = 25.0
        elif ext_ratio >= case.min_extraction_ratio:
            score_ext = 15.0
        else:
            score_ext = 5.0
        score += score_ext
        details["extraction_ratio"] = ext_ratio

        # 3. quality_counts (20点)
        qc = summary.get("quality_counts", {})
        total_q = sum(qc.values()) if qc else 1
        high = qc.get("high", 0)
        medium = qc.get("medium", 0)
        empty = qc.get("empty", 0)
        useful_ratio = (high + medium) / total_q if total_q > 0 else 0
        empty_ratio = empty / total_q if total_q > 0 else 0
        score_quality = min(20.0, useful_ratio * 20.0)
        if empty_ratio > case.max_empty_ratio:
            score_quality *= 0.5
        score += score_quality
        details["useful_ratio"] = round(useful_ratio, 3)
        details["empty_ratio"] = round(empty_ratio, 3)

        # 4. 検証通過 (20点)
        verified = summary.get("verified_success", False)
        checks = summary.get("verification_checks", {})
        passed_checks = sum(1 for v in checks.values() if v)
        total_checks = len(checks) if checks else 1
        score_verify = (passed_checks / total_checks) * 20.0 if verified else 10.0
        score += score_verify
        details["verified_success"] = verified
        details["checks_passed"] = f"{passed_checks}/{total_checks}"

        passed = score >= 60.0  # 60点以上で合格
        return BenchmarkResult(
            case=case.query,
            passed=passed,
            score=round(score, 1),
            details=details,
        )
