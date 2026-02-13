# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase 2 Handler (NORMALIZE)
クレーム正規化: RawClaim → NormalizedClaim変換 → Gap分析

LLM責務:
- RawClaim → NormalizedClaim変換（スロット推定）
- Gap（知識の穴）の言語化
- sub_questions生成

純粋ロジック:
- claim_id生成
- 重複統合
- 件数制限
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal
from ..models import generate_claim_id_from_text
from ..tool_trace import call_tool


def make_normalize_handler(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Callable[[ResearchRunContext], PhaseResult]:
    """
    Phase 2 (NORMALIZE) ハンドラを生成
    """
    cfg = config or {}
    max_claims = cfg.get("max_normalized_claims", 20)
    max_sub_questions = cfg.get("max_sub_questions", 10)
    
    def handler(context: ResearchRunContext) -> PhaseResult:
        """Phase 2: クレーム正規化"""
        raw_claims = context.raw_claims
        
        if not raw_claims:
            return PhaseResult(
                phase=Phase.NORMALIZE,
                success=True,
                signal=PhaseSignal.NEXT,
                output={"normalized_claims_count": 0},
                notes="Phase 2: RawClaimがないためスキップ"
            )
        
        try:
            normalized_claims: List[Dict] = []
            gaps: List[Dict] = []
            sub_questions: List[str] = []
            
            # 1. RawClaim → NormalizedClaim変換
            seen_fingerprints: set = set()
            
            for raw in raw_claims:
                fingerprint = raw.get("fingerprint", "")
                if fingerprint in seen_fingerprints:
                    continue  # 重複スキップ
                seen_fingerprints.add(fingerprint)
                
                # LLMで正規化
                if llm and hasattr(llm, "normalize_claim"):
                    normalized = call_tool(
                        context,
                        tool_name="llm.normalize_claim",
                        call=lambda raw=raw: llm.normalize_claim(raw),
                        args={"raw_claim_fingerprint": raw.get("fingerprint", "")},
                        result_summary=lambda row: {
                            "has_statement": bool((row or {}).get("statement")),
                        } if isinstance(row, dict) else {"type": type(row).__name__},
                    )
                else:
                    # スタブ: そのまま変換
                    normalized = _stub_normalize(raw)
                
                if normalized:
                    # claim_id付与
                    normalized["claim_id"] = normalized.get("claim_id") or generate_claim_id_from_text(
                        normalized.get("statement", raw.get("claim_text", ""))
                    )
                    normalized_claims.append(normalized)
                
                if len(normalized_claims) >= max_claims:
                    break
            
            # 2. Gap分析（知識の穴を特定）
            if llm and hasattr(llm, "analyze_gaps"):
                gaps = call_tool(
                    context,
                    tool_name="llm.analyze_gaps",
                    call=lambda: llm.analyze_gaps(normalized_claims, context.query),
                    args={
                        "query": context.query,
                        "normalized_claims_count": len(normalized_claims),
                    },
                    result_summary=lambda rows: {"gaps_count": len(rows or [])},
                )
            else:
                # スタブ: 基本的なGap
                gaps = _stub_gaps(normalized_claims, context.query)
            
            # 3. sub_questions生成
            if llm and hasattr(llm, "generate_sub_questions"):
                sub_questions = call_tool(
                    context,
                    tool_name="llm.generate_sub_questions",
                    call=lambda: llm.generate_sub_questions(
                        normalized_claims, gaps, context.query
                    ),
                    args={
                        "query": context.query,
                        "gaps_count": len(gaps),
                        "normalized_claims_count": len(normalized_claims),
                    },
                    result_summary=lambda rows: {"sub_questions_count": len(rows or [])},
                )[:max_sub_questions]
            else:
                # スタブ: Gapからサブ質問生成
                sub_questions = _stub_sub_questions(gaps, context.query)[:max_sub_questions]
            
            # 4. コンテキストに出力を格納
            context.normalized_claims = normalized_claims
            context.gaps = gaps
            context.sub_questions = sub_questions
            
            return PhaseResult(
                phase=Phase.NORMALIZE,
                success=True,
                signal=PhaseSignal.NEXT,
                output={
                    "normalized_claims_count": len(normalized_claims),
                    "gaps_count": len(gaps),
                    "sub_questions_count": len(sub_questions)
                },
                notes=f"Phase 2完了: {len(normalized_claims)}件正規化, {len(sub_questions)}サブ質問"
            )
            
        except Exception as e:
            return PhaseResult(
                phase=Phase.NORMALIZE,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
    
    return handler


def _stub_normalize(raw: Dict) -> Dict:
    """スタブ正規化"""
    return {
        "statement": raw.get("claim_text", ""),
        "scope": "general",
        "decision_relevance": "medium",
        "source_ids": [raw.get("source_url", "")],
        "created_at": datetime.now().isoformat()
    }


def _stub_gaps(normalized_claims: List[Dict], query: str) -> List[Dict]:
    """スタブGap生成"""
    return [
        {
            "gap_id": f"gap_{i}",
            "description": f"{query}に関する追加調査が必要",
            "state": "OPEN",
            "related_claims": [c.get("claim_id") for c in normalized_claims[:3]]
        }
        for i in range(min(3, max(1, len(normalized_claims) // 5)))
    ]


def _stub_sub_questions(gaps: List[Dict], query: str) -> List[str]:
    """スタブサブ質問生成"""
    questions = [f"{query}の詳細は？"]
    for gap in gaps:
        desc = gap.get("description", "")
        if desc:
            questions.append(f"{desc}の根拠は？")
    return questions
