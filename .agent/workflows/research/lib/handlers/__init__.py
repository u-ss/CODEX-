# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Handlers Package
各Phaseのハンドラ実装

GPT-5.2設計:
- handlers/配下にPhase別ファイル
- make_default_handlers()で登録表を返す
- LLM部分と純粋ロジックを分離
"""

from typing import Dict, Callable, Any, Optional

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal


def make_default_handlers(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Dict[Phase, Callable[[ResearchRunContext], PhaseResult]]:
    """
    デフォルトハンドラの登録表を生成
    
    Args:
        tools: ツール呼び出しインターフェース（search_web, read_url_content等）
        llm: LLM呼び出しインターフェース
        config: 設定辞書
    
    Returns:
        Phase → ハンドラのマッピング
    """
    from .phase1_wide import make_wide_handler
    from .phase2_normalize import make_normalize_handler
    from .phase3_deep import make_deep_handler
    from .phase35_verify import make_verify_handler
    from .phase4_integrate import make_integrate_handler
    
    cfg = config or {}
    
    return {
        Phase.WIDE: make_wide_handler(tools, llm, cfg),
        Phase.NORMALIZE: make_normalize_handler(tools, llm, cfg),
        Phase.DEEP: make_deep_handler(tools, llm, cfg),
        Phase.VERIFY: make_verify_handler(tools, llm, cfg),
        Phase.INTEGRATE: make_integrate_handler(tools, llm, cfg),
    }


# 便利なエクスポート
__all__ = [
    "make_default_handlers",
    "Phase",
    "PhaseResult",
    "PhaseSignal",
]
