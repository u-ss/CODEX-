# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.4 - Verify Module
VERIFYゲート評価器 + 受け入れ条件検証

CODEX Review 指摘対応:
- GateEvaluator: 各フェーズ完了時に通過条件を評価
- ACVerifier: 受け入れ条件の充足度を判定
- VerdictLogger: 判定結果をログに記録

v4.2.4 変更点:
- GatePhase enumでphaseキーを型安全化
- ACVerifier.verify()でindex範囲外をgraceful処理
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class GateStatus(Enum):
    """ゲート状態"""
    PASSED = "passed"      # 通過
    FAILED = "failed"      # 不合格
    SKIPPED = "skipped"    # スキップ（該当なし）
    PENDING = "pending"    # 未評価


class GatePhase(Enum):
    """ゲートフェーズ（型安全化）"""
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"


@dataclass
class GateCondition:
    """ゲート条件"""
    name: str
    description: str
    required: bool = True  # 必須か


@dataclass
class GateEvaluation:
    """ゲート評価結果"""
    gate_name: str
    status: GateStatus
    conditions_met: List[str] = field(default_factory=list)
    conditions_failed: List[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    details: str = ""


# フェーズ別ゲート定義（GatePhase enumをキーに使用）
PHASE_GATES = {
    GatePhase.PLAN: [
        GateCondition("scope_defined", "対象範囲が明確に定義されている"),
        GateCondition("ac_defined", "受け入れ条件が1つ以上定義されている"),
        GateCondition("evidence_linked", "変更対象にEvidenceが紐づいている"),
    ],
    GatePhase.IMPLEMENT: [
        GateCondition("code_valid", "コードが構文的に正しい"),
        GateCondition("lint_passed", "Lint/型チェックをパス"),
        GateCondition("diff_tests_passed", "差分関連テストをパス"),
    ],
    GatePhase.VERIFY: [
        GateCondition("smoke_passed", "スモークテストをパス"),
        GateCondition("ac_satisfied", "受け入れ条件を全て満たす"),
        GateCondition("regression_none", "リグレッションなし"),
    ],
}

# 文字列→GatePhase変換（後方互換）
GATE_PHASE_MAP = {
    "plan": GatePhase.PLAN,
    "implement": GatePhase.IMPLEMENT,
    "verify": GatePhase.VERIFY,
}


class GateEvaluator:
    """ゲート評価器"""
    
    def __init__(self):
        self.evaluations: Dict[str, GateEvaluation] = {}
    
    def evaluate(
        self,
        phase: str,
        results: Dict[str, bool]
    ) -> GateEvaluation:
        """
        フェーズのゲートを評価
        
        Args:
            phase: フェーズ名（plan/implement/verify）または GatePhase
            results: {条件名: 充足状態}
        
        Returns:
            GateEvaluation
        """
        # 文字列→GatePhase変換（後方互換）
        if isinstance(phase, str):
            gate_phase = GATE_PHASE_MAP.get(phase)
            if gate_phase is None:
                return GateEvaluation(
                    gate_name=phase,
                    status=GateStatus.SKIPPED,
                    details=f"Unknown phase: {phase}"
                )
        else:
            gate_phase = phase
        
        gates = PHASE_GATES.get(gate_phase, [])
        met = []
        failed = []
        
        for gate in gates:
            if gate.name in results:
                if results[gate.name]:
                    met.append(gate.name)
                else:
                    if gate.required:
                        failed.append(gate.name)
            elif gate.required:
                # 必須条件が未評価 → 失敗扱い
                failed.append(f"{gate.name} (not_evaluated)")
        
        status = GateStatus.PASSED if len(failed) == 0 else GateStatus.FAILED
        
        # gate_nameは文字列で保存（後方互換）
        gate_name = gate_phase.value if isinstance(gate_phase, GatePhase) else str(phase)
        
        eval_result = GateEvaluation(
            gate_name=gate_name,
            status=status,
            conditions_met=met,
            conditions_failed=failed,
            details=f"met={len(met)}, failed={len(failed)}"
        )
        
        self.evaluations[gate_name] = eval_result
        return eval_result
    
    def all_passed(self) -> bool:
        """全ゲートがパスしたか"""
        return all(
            e.status == GateStatus.PASSED 
            for e in self.evaluations.values()
        )
    
    def get_blocking_gates(self) -> List[str]:
        """ブロックしているゲートを取得"""
        return [
            name for name, e in self.evaluations.items()
            if e.status == GateStatus.FAILED
        ]


@dataclass
class ACResult:
    """受け入れ条件結果"""
    ac_id: str
    description: str
    satisfied: bool
    evidence: str = ""


class ACVerifier:
    """受け入れ条件検証器"""
    
    def __init__(self, acceptance_criteria: List[str]):
        self.criteria = acceptance_criteria
        self.results: List[ACResult] = []
    
    def verify(self, ac_id: str, satisfied: bool, evidence: str = "") -> None:
        """
        条件を検証
        
        Args:
            ac_id: 条件ID（数字または文字列）
            satisfied: 充足したか
            evidence: 証拠
        """
        # v4.2.4: index安全化 - 範囲外をgraceful処理
        if ac_id.isdigit():
            idx = int(ac_id) - 1
            if 0 <= idx < len(self.criteria):
                desc = self.criteria[idx]
            else:
                desc = f"AC#{ac_id} (index out of range: {idx+1}/{len(self.criteria)})"
        else:
            desc = ac_id
        self.results.append(ACResult(ac_id, desc, satisfied, evidence))
    
    def all_satisfied(self) -> bool:
        """全条件を満たすか"""
        return len(self.results) >= len(self.criteria) and all(r.satisfied for r in self.results)
    
    def satisfaction_rate(self) -> float:
        """充足率"""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.satisfied) / len(self.criteria)
    
    def get_unsatisfied(self) -> List[ACResult]:
        """未充足条件を取得"""
        return [r for r in self.results if not r.satisfied]


@dataclass
class VerdictLog:
    """判定ログエントリ"""
    phase: str
    verdict: str  # "pass" / "fail" / "skip"
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class VerdictLogger:
    """判定ログ"""
    
    def __init__(self):
        self.logs: List[VerdictLog] = []
    
    def log(self, phase: str, verdict: str, reason: str) -> None:
        """判定を記録"""
        self.logs.append(VerdictLog(phase, verdict, reason))
    
    def get_summary(self) -> Dict[str, int]:
        """サマリを取得"""
        summary = {"pass": 0, "fail": 0, "skip": 0}
        for log in self.logs:
            if log.verdict in summary:
                summary[log.verdict] += 1
        return summary
    
    def format_report(self) -> str:
        """レポートフォーマット"""
        lines = ["=" * 40, "Verdict Log", "=" * 40]
        for log in self.logs:
            icon = "✅" if log.verdict == "pass" else "❌" if log.verdict == "fail" else "⏭️"
            lines.append(f"{icon} [{log.phase}] {log.verdict.upper()}: {log.reason}")
        
        summary = self.get_summary()
        lines.append("")
        lines.append(f"Summary: ✅{summary['pass']} ❌{summary['fail']} ⏭️{summary['skip']}")
        return "\n".join(lines)
