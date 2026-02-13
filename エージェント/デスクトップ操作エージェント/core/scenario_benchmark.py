"""
Scenario Benchmark（シナリオベンチ）

目的: 評価・回帰テスト基盤（初見対応/状況把握の改善を測定）

ChatGPT 5.2フィードバック（2026-02-05 Round6）より:
「初見対応/状況把握は、実装を増やすより『何がどれだけ改善したか』を継続測定できるかで決まります」

設計:
- シナリオベンチ: 初見アプリ/初見画面を含むタスクセット
- 指標（自動集計）: 確度推移/誤認回数/probe回数/観測コスト
- リプレイ: 同じログ・同じ観測で再実行して回帰確認
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Callable
import json
import time
from pathlib import Path


class ScenarioType(Enum):
    """シナリオタイプ"""
    KNOWN_APP = "known_app"       # 既知アプリ
    UNKNOWN_APP = "unknown_app"   # 未知アプリ（初見）
    KNOWN_FLOW = "known_flow"     # 既知フロー
    UNKNOWN_FLOW = "unknown_flow" # 未知フロー


class TaskStep(Enum):
    """タスクステップタイプ"""
    NAVIGATE = "navigate"     # ナビゲーション
    CLICK = "click"           # クリック
    INPUT = "input"           # 入力
    WAIT = "wait"             # 待機
    VERIFY = "verify"         # 検証
    SEARCH = "search"         # 検索


@dataclass
class BenchmarkStep:
    """ベンチマークステップ"""
    step_type: TaskStep
    target: str                  # セレクタ/URL/テキスト
    expected_result: str         # 期待結果
    timeout_ms: int = 5000
    
    def to_dict(self) -> dict:
        return {
            "type": self.step_type.value,
            "target": self.target,
            "expected": self.expected_result,
            "timeout": self.timeout_ms,
        }


@dataclass
class BenchmarkScenario:
    """ベンチマークシナリオ"""
    name: str
    scenario_type: ScenarioType
    app_name: str
    steps: list[BenchmarkStep] = field(default_factory=list)
    description: str = ""
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.scenario_type.value,
            "app": self.app_name,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class StepResult:
    """ステップ結果"""
    step_index: int
    success: bool
    duration_ms: int
    confidence_before: float
    confidence_after: float
    probe_count: int           # 観測回数
    probe_types: list[str]     # 観測タイプ（dom/uia/ss）
    error_message: str = ""
    
    @property
    def confidence_delta(self) -> float:
        return self.confidence_after - self.confidence_before
    
    @property
    def had_reprobe(self) -> bool:
        return self.probe_count > 1


@dataclass
class BenchmarkMetrics:
    """ベンチマーク指標"""
    # 基本
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    
    # 確度
    avg_confidence: float = 0.0
    confidence_variance: float = 0.0
    belief_flips: int = 0        # 確度が反転した回数
    
    # 観測
    total_probes: int = 0
    cheap_probes: int = 0        # 軽量観測（DOM等）
    heavy_probes: int = 0        # 重量観測（SS等）
    avg_probe_cost_ms: float = 0.0
    
    # 時間
    total_duration_ms: int = 0
    avg_step_duration_ms: float = 0.0
    
    def success_rate(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return self.successful_steps / self.total_steps
    
    def format(self) -> str:
        lines = [
            "📊 Benchmark Metrics:",
            f"  成功率: {self.success_rate():.0%} ({self.successful_steps}/{self.total_steps})",
            f"  平均確度: {self.avg_confidence:.0%}",
            f"  確度反転: {self.belief_flips}回",
            f"  観測回数: {self.total_probes} (軽量:{self.cheap_probes} / 重量:{self.heavy_probes})",
            f"  観測コスト: {self.avg_probe_cost_ms:.0f}ms/回",
            f"  総時間: {self.total_duration_ms}ms",
        ]
        return "\n".join(lines)


@dataclass
class BenchmarkRun:
    """ベンチマーク実行結果"""
    scenario: BenchmarkScenario
    step_results: list[StepResult] = field(default_factory=list)
    metrics: BenchmarkMetrics = field(default_factory=BenchmarkMetrics)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    
    def calculate_metrics(self) -> BenchmarkMetrics:
        """指標を計算"""
        m = BenchmarkMetrics()
        m.total_steps = len(self.step_results)
        
        if not self.step_results:
            return m
        
        confidences = []
        durations = []
        
        for sr in self.step_results:
            if sr.success:
                m.successful_steps += 1
            else:
                m.failed_steps += 1
            
            confidences.append(sr.confidence_after)
            durations.append(sr.duration_ms)
            m.total_probes += sr.probe_count
            
            # 軽量/重量分類
            for pt in sr.probe_types:
                if pt in ["dom", "uia"]:
                    m.cheap_probes += 1
                else:
                    m.heavy_probes += 1
            
            # 確度反転チェック
            if sr.confidence_delta < -0.2:
                m.belief_flips += 1
        
        m.avg_confidence = sum(confidences) / len(confidences)
        m.total_duration_ms = sum(durations)
        m.avg_step_duration_ms = m.total_duration_ms / len(durations)
        
        if m.total_probes > 0:
            m.avg_probe_cost_ms = m.total_duration_ms / m.total_probes
        
        self.metrics = m
        return m


class ScenarioBenchmark:
    """シナリオベンチマーク"""
    
    def __init__(self, output_dir: str = "."):
        self.output_dir = Path(output_dir)
        self.scenarios: list[BenchmarkScenario] = []
        self.runs: list[BenchmarkRun] = []
        
        # サンプルシナリオを登録
        self._register_sample_scenarios()
    
    def _register_sample_scenarios(self) -> None:
        """サンプルシナリオを登録"""
        # ChatGPT: 既知フロー
        self.scenarios.append(BenchmarkScenario(
            name="chatgpt_basic_chat",
            scenario_type=ScenarioType.KNOWN_FLOW,
            app_name="ChatGPT",
            description="ChatGPTで基本的なチャットを送信",
            steps=[
                BenchmarkStep(TaskStep.NAVIGATE, "https://chatgpt.com/", "ChatGPT画面表示"),
                BenchmarkStep(TaskStep.WAIT, "#prompt-textarea", "入力欄表示"),
                BenchmarkStep(TaskStep.INPUT, "#prompt-textarea", "テスト入力完了"),
                BenchmarkStep(TaskStep.CLICK, "[data-testid='send-button']", "送信完了"),
                BenchmarkStep(TaskStep.WAIT, ".response", "応答受信"),
            ]
        ))
        
        # 汎用検索: 初見対応テスト
        self.scenarios.append(BenchmarkScenario(
            name="generic_search_flow",
            scenario_type=ScenarioType.UNKNOWN_FLOW,
            app_name="Generic",
            description="初見サイトで検索を実行",
            steps=[
                BenchmarkStep(TaskStep.SEARCH, "search", "検索欄発見"),
                BenchmarkStep(TaskStep.INPUT, "search_input", "キーワード入力"),
                BenchmarkStep(TaskStep.CLICK, "submit", "検索実行"),
                BenchmarkStep(TaskStep.VERIFY, "results", "結果表示"),
            ]
        ))
    
    def run_scenario(
        self,
        scenario: BenchmarkScenario,
        executor: Optional[Callable] = None
    ) -> BenchmarkRun:
        """シナリオを実行"""
        run = BenchmarkRun(scenario=scenario)
        
        for i, step in enumerate(scenario.steps):
            start = time.time()
            
            # モック実行（実際にはexecutorを使用）
            if executor:
                result = executor(step)
            else:
                result = self._mock_execute(step)
            
            duration = int((time.time() - start) * 1000)
            
            step_result = StepResult(
                step_index=i,
                success=result.get("success", False),
                duration_ms=duration,
                confidence_before=result.get("conf_before", 0.5),
                confidence_after=result.get("conf_after", 0.5),
                probe_count=result.get("probes", 1),
                probe_types=result.get("probe_types", ["dom"]),
                error_message=result.get("error", "")
            )
            
            run.step_results.append(step_result)
        
        run.finished_at = datetime.now().isoformat()
        run.calculate_metrics()
        
        self.runs.append(run)
        return run
    
    def _mock_execute(self, step: BenchmarkStep) -> dict:
        """モック実行"""
        import random
        
        time.sleep(0.05)  # シミュレート
        
        return {
            "success": random.random() > 0.2,
            "conf_before": random.uniform(0.4, 0.7),
            "conf_after": random.uniform(0.5, 0.9),
            "probes": random.randint(1, 3),
            "probe_types": random.choices(["dom", "uia", "ss"], k=random.randint(1, 3)),
        }
    
    def compare_runs(self, run1: BenchmarkRun, run2: BenchmarkRun) -> dict:
        """2つの実行を比較"""
        m1, m2 = run1.metrics, run2.metrics
        
        return {
            "success_rate_delta": m2.success_rate() - m1.success_rate(),
            "confidence_delta": m2.avg_confidence - m1.avg_confidence,
            "belief_flips_delta": m2.belief_flips - m1.belief_flips,
            "probe_cost_delta": m2.avg_probe_cost_ms - m1.avg_probe_cost_ms,
            "duration_delta": m2.total_duration_ms - m1.total_duration_ms,
        }
    
    def save_run(self, run: BenchmarkRun) -> Path:
        """実行結果を保存"""
        filename = f"bench_{run.scenario.name}_{run.started_at[:10]}.json"
        path = self.output_dir / filename
        
        data = {
            "scenario": run.scenario.to_dict(),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "metrics": {
                "success_rate": run.metrics.success_rate(),
                "avg_confidence": run.metrics.avg_confidence,
                "belief_flips": run.metrics.belief_flips,
                "total_probes": run.metrics.total_probes,
                "total_duration_ms": run.metrics.total_duration_ms,
            },
            "step_results": [
                {
                    "index": sr.step_index,
                    "success": sr.success,
                    "duration_ms": sr.duration_ms,
                    "confidence_delta": sr.confidence_delta,
                }
                for sr in run.step_results
            ]
        }
        
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    
    def format_comparison(self, comparison: dict) -> str:
        """比較結果をフォーマット"""
        def arrow(delta: float) -> str:
            if delta > 0.05:
                return "⬆️"
            elif delta < -0.05:
                return "⬇️"
            else:
                return "➡️"
        
        lines = [
            "📈 Run Comparison:",
            f"  成功率: {arrow(comparison['success_rate_delta'])} {comparison['success_rate_delta']:+.0%}",
            f"  確度: {arrow(comparison['confidence_delta'])} {comparison['confidence_delta']:+.0%}",
            f"  確度反転: {arrow(-comparison['belief_flips_delta'])} {comparison['belief_flips_delta']:+d}回",
            f"  観測コスト: {arrow(-comparison['probe_cost_delta'])} {comparison['probe_cost_delta']:+.0f}ms",
            f"  総時間: {arrow(-comparison['duration_delta'])} {comparison['duration_delta']:+d}ms",
        ]
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Scenario Benchmark テスト")
    print("=" * 60)
    
    bench = ScenarioBenchmark()
    
    # サンプルシナリオ実行
    print("\n--- シナリオ実行1 ---")
    scenario = bench.scenarios[0]
    run1 = bench.run_scenario(scenario)
    print(run1.metrics.format())
    
    # 2回目実行（比較用）
    print("\n--- シナリオ実行2 ---")
    run2 = bench.run_scenario(scenario)
    print(run2.metrics.format())
    
    # 比較
    print("\n--- 比較 ---")
    comparison = bench.compare_runs(run1, run2)
    print(bench.format_comparison(comparison))
    
    print("\n" + "=" * 60)
    print("テスト完了")
