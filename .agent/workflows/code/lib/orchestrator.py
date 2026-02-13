# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.4 - Orchestrator Module
7-Phaseライブラリの統合オーケストレーター

CODEX Review 指摘対応:
- Phase入出力をRunContextに強制記録
- 各Phaseを逐次実行し、ゲート評価を挿入
- 失敗時のSelf-Healingフローを統合

v4.2.4 変更点:
- PHASE_TO_GATE_KEYをモジュール定数化
- 未対応phaseはValueError
- retry理由をctx.failuresに記録
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Callable, Any

from .context import RunContext
from .self_healing import classify_failure, should_retry, FailureRecord
from .verify import GateEvaluator, GateStatus


class Phase(Enum):
    """7-Phaseフロー"""
    RESEARCH = "research"
    PLAN = "plan"
    TEST = "test"
    CODE = "code"
    DEBUG = "debug"
    VERIFY = "verify"
    DOCUMENT = "document"


# Phase→Gate名マッピング（モジュール定数）
# verify.pyのPHASE_GATESキー（plan/implement/verify）に対応
PHASE_TO_GATE_KEY: Dict[Phase, str] = {
    Phase.PLAN: "plan",
    Phase.CODE: "implement",
    Phase.VERIFY: "verify",
}


@dataclass
class PhaseResult:
    """フェーズ実行結果"""
    phase: Phase
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class PhaseRecord:
    """フェーズ実行記録"""
    phase: Phase
    started_at: str
    finished_at: Optional[str] = None
    result: Optional[PhaseResult] = None
    retry_count: int = 0


class Orchestrator:
    """
    7-Phaseオーケストレーター
    
    各フェーズを逐次実行し、RunContextに記録しながら進行。
    失敗時はSelf-Healingを試行し、ゲート評価で進行可否を判定。
    """
    
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.current_phase: Phase = Phase.RESEARCH
        self.records: Dict[Phase, PhaseRecord] = {}
        self.gate_evaluator = GateEvaluator()
        
        # フェーズハンドラ（外部から登録）
        self.handlers: Dict[Phase, Callable[[RunContext], PhaseResult]] = {}
    
    def register_handler(self, phase: Phase, handler: Callable[[RunContext], PhaseResult]) -> None:
        """フェーズハンドラを登録"""
        self.handlers[phase] = handler
    
    def run_phase(self, phase: Phase) -> PhaseResult:
        """
        単一フェーズを実行
        
        Args:
            phase: 実行するフェーズ
        
        Returns:
            PhaseResult
        """
        # 記録開始
        record = PhaseRecord(phase=phase, started_at=datetime.now().isoformat())
        self.records[phase] = record
        
        # ハンドラ取得
        handler = self.handlers.get(phase)
        if not handler:
            return PhaseResult(
                phase=phase,
                success=False,
                error=f"No handler registered for phase: {phase.value}"
            )
        
        # 実行
        start_time = datetime.now()
        try:
            result = handler(self.ctx)
        except Exception as e:
            result = PhaseResult(
                phase=phase,
                success=False,
                error=str(e)
            )
        
        # 記録完了
        result.duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        record.finished_at = datetime.now().isoformat()
        record.result = result
        
        # RunContextに記録
        self._record_to_context(phase, result)
        
        return result
    
    def _record_to_context(self, phase: Phase, result: PhaseResult) -> None:
        """RunContextにフェーズ結果を記録"""
        # phase_resultsはRunContext.dataclassに正式定義済み（v4.2.4）
        self.ctx.phase_results[phase.value] = {
            "success": result.success,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "output_summary": str(result.output)[:200] if result.output else None
        }
    
    def run_with_healing(
        self,
        phase: Phase,
        max_retries: int = 3
    ) -> PhaseResult:
        """
        Self-Healing付きでフェーズを実行
        
        Args:
            phase: フェーズ
            max_retries: 最大リトライ回数
        
        Returns:
            PhaseResult
        """
        for attempt in range(1, max_retries + 1):
            result = self.run_phase(phase)
            
            if result.success:
                return result
            
            # 失敗分類
            category = classify_failure(
                stderr=result.error or "",
                exit_code=1,
                phase=phase.value
            )
            
            # リトライ判定
            failure_record = FailureRecord(
                phase=phase.value,
                command=f"phase_{phase.value}",
                signature_hash="",
                category=category,
                attempt=attempt,
                first_seen_at=datetime.now().isoformat(),
                last_seen_at=datetime.now().isoformat(),
                stderr_snippet=result.error or ""
            )
            
            retry, reason = should_retry(failure_record, max_retries)
            
            # v4.2.4: retry理由をctx.failuresに記録（監査用）
            self.ctx.append_failure({
                "phase": phase.value,
                "attempt": attempt,
                "reason": reason,
                "will_retry": retry,
                "category": category.value,
                "timestamp": datetime.now().isoformat()
            })
            
            if not retry:
                # リトライ不可 → 終了
                return result
            
            # リトライ
            self.records[phase].retry_count = attempt
        
        return result
    
    def evaluate_gate(self, phase: Phase, results: Dict[str, bool]) -> bool:
        """
        フェーズのゲートを評価
        
        Args:
            phase: フェーズ
            results: {条件名: 充足状態}
        
        Returns:
            ゲート通過したか
        
        Raises:
            ValueError: 未対応phaseの場合
        """
        # v4.2.4: モジュール定数を使用、未対応は明示エラー
        if phase not in PHASE_TO_GATE_KEY:
            raise ValueError(f"No gate defined for phase: {phase.value}")
        gate_key = PHASE_TO_GATE_KEY[phase]
        evaluation = self.gate_evaluator.evaluate(gate_key, results)
        return evaluation.status == GateStatus.PASSED
    
    def run_all(self) -> Dict[Phase, PhaseResult]:
        """
        全フェーズを順次実行
        
        Returns:
            {Phase: PhaseResult}
        """
        all_results = {}
        phases = [
            Phase.RESEARCH,
            Phase.PLAN,
            Phase.TEST,
            Phase.CODE,
            Phase.DEBUG,
            Phase.VERIFY,
            Phase.DOCUMENT,
        ]
        
        for phase in phases:
            result = self.run_with_healing(phase)
            all_results[phase] = result
            
            if not result.success:
                # フェーズ失敗で中断
                break
            
            self.current_phase = phase
        
        return all_results
    
    def get_summary(self) -> str:
        """実行サマリを取得"""
        lines = ["=" * 40, "Orchestrator Summary", "=" * 40]
        
        for phase, record in self.records.items():
            if record.result:
                icon = "✅" if record.result.success else "❌"
                retry = f" (retry:{record.retry_count})" if record.retry_count > 0 else ""
                lines.append(f"{icon} {phase.value}{retry}: {record.result.duration_ms}ms")
        
        return "\n".join(lines)
