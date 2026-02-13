# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase Runner Module
状態機械ベースのPhase遷移制御

GPT-5.2設計: 遷移表で明示的にPhase間の流れを制御
- VERIFY → DEEP への差し戻し（ROLLBACK）
- 終了条件による自動遷移
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Any

from .context import ResearchRunContext


class Phase(str, Enum):
    """リサーチフェーズ"""
    WIDE = "wide"              # Phase 1: 広域調査
    NORMALIZE = "reasoning"     # Phase 2: クレーム正規化
    DEEP = "deep"              # Phase 3: 深層調査
    VERIFY = "verification"    # Phase 3.5: 検証
    INTEGRATE = "synthesis"    # Phase 4: 統合
    COMPLETE = "complete"      # 完了


class PhaseSignal(str, Enum):
    """Phase実行結果のシグナル"""
    NEXT = "next"           # 次のPhaseへ進む
    CONTINUE = "continue"   # 同じPhaseを継続（ラウンド制）
    RETRY = "retry"         # 同じPhaseをリトライ
    ROLLBACK = "rollback"   # 前のPhaseに戻る
    ABORT = "abort"         # 中止


@dataclass
class PhaseResult:
    """Phase実行結果"""
    phase: Phase
    success: bool
    signal: PhaseSignal
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    rollback_to: Optional[Phase] = None
    required_actions: List[Dict[str, Any]] = field(default_factory=list)
    notes: str = ""


# Phase遷移表
TRANSITIONS: Dict[Phase, Phase] = {
    Phase.WIDE: Phase.NORMALIZE,
    Phase.NORMALIZE: Phase.DEEP,
    Phase.DEEP: Phase.VERIFY,
    Phase.VERIFY: Phase.INTEGRATE,
    Phase.INTEGRATE: Phase.COMPLETE,
}


class PhaseRunner:
    """
    状態機械ベースのPhase実行エンジン
    
    使用例:
        runner = PhaseRunner()
        runner.register(Phase.WIDE, wide_handler)
        runner.register(Phase.NORMALIZE, normalize_handler)
        ...
        
        while runner.current_phase != Phase.COMPLETE:
            result = runner.run_current(context)
            runner.transition(result)
    """
    
    def __init__(self):
        self.handlers: Dict[Phase, Callable[[ResearchRunContext], PhaseResult]] = {}
        self.current_phase: Phase = Phase.WIDE
        self.history: List[PhaseResult] = []
    
    def register(self, phase: Phase, handler: Callable[[ResearchRunContext], PhaseResult]):
        """Phaseハンドラを登録"""
        self.handlers[phase] = handler
    
    def run_current(self, context: ResearchRunContext) -> PhaseResult:
        """現在のPhaseを実行"""
        handler = self.handlers.get(self.current_phase)
        if not handler:
            return PhaseResult(
                phase=self.current_phase,
                success=False,
                signal=PhaseSignal.ABORT,
                error=f"No handler registered for {self.current_phase}"
            )
        
        try:
            result = handler(context)
        except Exception as e:
            result = PhaseResult(
                phase=self.current_phase,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
        
        self.history.append(result)
        return result
    
    def transition(self, result: PhaseResult) -> Phase:
        """
        結果に基づいてPhaseを遷移
        
        Returns:
            遷移後のPhase
        """
        if result.signal == PhaseSignal.NEXT:
            # 次のPhaseへ
            self.current_phase = TRANSITIONS.get(self.current_phase, Phase.COMPLETE)
        
        elif result.signal == PhaseSignal.CONTINUE:
            # 同じPhaseを継続（遷移なし）
            pass
        
        elif result.signal == PhaseSignal.ROLLBACK:
            # 指定されたPhaseへ戻る
            if result.rollback_to:
                self.current_phase = result.rollback_to
            else:
                # デフォルト: VERIFY → DEEP
                if self.current_phase == Phase.VERIFY:
                    self.current_phase = Phase.DEEP
        
        elif result.signal == PhaseSignal.RETRY:
            # 同じPhaseをリトライ（遷移なし）
            pass
        
        elif result.signal == PhaseSignal.ABORT:
            # 中止 → COMPLETE（異常終了）
            self.current_phase = Phase.COMPLETE
        
        return self.current_phase
    
    def get_phase_name(self) -> str:
        """現在のPhase名を取得（日本語）"""
        names = {
            Phase.WIDE: "Phase 1: 広域調査",
            Phase.NORMALIZE: "Phase 2: クレーム正規化",
            Phase.DEEP: "Phase 3: 深層調査",
            Phase.VERIFY: "Phase 3.5: 検証",
            Phase.INTEGRATE: "Phase 4: 統合",
            Phase.COMPLETE: "完了"
        }
        return names.get(self.current_phase, str(self.current_phase))


# デフォルトハンドラ（スタブ）
def stub_handler(phase: Phase) -> Callable[[ResearchRunContext], PhaseResult]:
    """スタブハンドラを生成（実装前のプレースホルダー）"""
    def handler(context: ResearchRunContext) -> PhaseResult:
        return PhaseResult(
            phase=phase,
            success=True,
            signal=PhaseSignal.NEXT,
            notes=f"{phase.value} stub executed"
        )
    return handler
