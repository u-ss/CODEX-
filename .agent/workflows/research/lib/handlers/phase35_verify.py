# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase 3.5 Handler (VERIFY)
検証: 主要Claim選定 → 反証探索 → ステータス判定 → 差し戻し判定

LLM責務:
- 反証探索クエリ生成
- 反証要約

純粋ロジック:
- 主要Claim選定（top-k）
- status判定（verification.determine_status）
- ROLLBACK条件判定
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal
from ..verification import determine_status, is_contested
from ..quality_gate import validate_phase35
from ..locator import is_strong_locator
from ..tool_trace import call_tool


def make_verify_handler(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Callable[[ResearchRunContext], PhaseResult]:
    """
    Phase 3.5 (VERIFY) ハンドラを生成
    """
    cfg = config or {}
    top_k_claims = cfg.get("top_k_claims", 10)
    min_evidence_for_verify = cfg.get("min_evidence_for_verify", 1)
    max_counter_queries = cfg.get("max_counter_queries", 4)
    
    def handler(context: ResearchRunContext) -> PhaseResult:
        """Phase 3.5: 検証"""
        normalized_claims = context.normalized_claims
        evidence = context.evidence
        
        if not normalized_claims:
            return PhaseResult(
                phase=Phase.VERIFY,
                success=True,
                signal=PhaseSignal.NEXT,
                output={"verified_claims_count": 0},
                notes="Phase 3.5: Claimがないためスキップ"
            )
        
        try:
            verified_claims: List[Dict] = []
            counterevidence_log: List[Dict] = []
            required_actions: List[Dict] = []
            
            # 1. 主要Claim選定（上位k件）
            top_claims = _select_top_claims(normalized_claims, evidence, top_k_claims)
            
            # 2. 各Claimを検証
            for claim in top_claims:
                claim_id = claim.get("claim_id", "")
                claim_text = claim.get("statement", "")
                
                # 関連Evidenceを取得
                related_evidence = _get_related_evidence(claim_id, evidence)
                # locator無しEvidenceは採用不可（監査可能性優先）
                related_evidence = [
                    e for e in related_evidence
                    if (e.get("quote") or "").strip() and is_strong_locator(e.get("locator"))
                ]
                
                # Evidence不足なら差し戻し候補
                if len(related_evidence) < min_evidence_for_verify:
                    required_actions.append({
                        "type": "additional_search",
                        "query": f"{claim_text}の根拠",
                        "claim_id": claim_id,
                        "reason": "Evidence不足"
                    })
                    continue
                
                # 反証探索
                counter_result = _search_counterevidence(
                    context,
                    claim_id,
                    claim_text,
                    tools,
                    llm,
                    max_counter_queries=max_counter_queries,
                )
                counterevidence_log.append(counter_result)
                
                # ステータス判定
                pos_weight, neg_weight = _calculate_weights(
                    related_evidence, counter_result
                )
                has_primary = any(_is_primary_source(e) for e in related_evidence)
                telephone_risk = 0.25 if has_primary else 0.6
                
                # 信頼度計算（簡易版）
                confidence = (pos_weight - neg_weight) / max(pos_weight + neg_weight, 1)
                
                status = determine_status(
                    confidence=confidence,
                    pos_weight=pos_weight,
                    neg_weight=neg_weight,
                    has_primary_source=has_primary,
                    telephone_risk=telephone_risk
                )
                
                verified_claims.append({
                    "claim_id": claim_id,
                    "statement": claim_text,
                    "status": status.value,
                    "rationale": f"Evidence {len(related_evidence)}件, 信頼度 {confidence:.2f}",
                    "supporting_evidence_ids": [_evidence_ref(e) for e in related_evidence if e.get("stance") == "supports"],
                    "refuting_evidence_ids": [_evidence_ref(e) for e in related_evidence if e.get("stance") == "refutes"],
                    "verified_at": datetime.now().isoformat(),
                })
            
            # 3. コンテキストに出力を格納
            context.verified_claims = verified_claims
            context.counterevidence_log = counterevidence_log
            
            # 4. 品質ゲート（Phase 3.5）
            errors, warnings = validate_phase35(
                verified_claims=verified_claims,
                counterevidence_log=counterevidence_log,
                evidence=evidence
            )
            for err in errors:
                required_actions.append({
                    "type": "quality_gate",
                    "query": err,
                    "reason": "Quality gate error"
                })
            
            # 5. 差し戻し判定
            if required_actions and context.can_rollback():
                context.required_actions = required_actions
                return PhaseResult(
                    phase=Phase.VERIFY,
                    success=True,
                    signal=PhaseSignal.ROLLBACK,
                    rollback_to=Phase.DEEP,
                    required_actions=required_actions,
                    output={
                        "verified_claims_count": len(verified_claims),
                        "rollback_reason": "品質ゲート/証拠不足のClaimあり",
                        "warnings": warnings[:10]
                    },
                    notes=f"Phase 3.5: {len(required_actions)}件追加調査が必要 → Phase 3へ差し戻し"
                )
            
            return PhaseResult(
                phase=Phase.VERIFY,
                success=True,
                signal=PhaseSignal.NEXT,
                output={
                    "verified_claims_count": len(verified_claims),
                    "counterevidence_searches": len(counterevidence_log),
                    "warnings": warnings[:10]
                },
                notes=f"Phase 3.5完了: {len(verified_claims)}件検証"
            )
            
        except Exception as e:
            return PhaseResult(
                phase=Phase.VERIFY,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
    
    return handler


def _select_top_claims(
    claims: List[Dict],
    evidence: List[Dict],
    k: int
) -> List[Dict]:
    """上位k件のClaimを選定（Evidence数でソート）"""
    def evidence_count(claim):
        claim_id = claim.get("claim_id", "")
        return sum(
            1 for e in evidence
            if isinstance(e, dict) and claim_id and claim_id in (e.get("claim_ids") or [])
        )
    
    sorted_claims = sorted(claims, key=evidence_count, reverse=True)
    return sorted_claims[:k]


def _get_related_evidence(claim_id: str, evidence: List[Dict]) -> List[Dict]:
    """ClaimIDに関連するEvidenceを取得"""
    related = [
        e for e in evidence
        if isinstance(e, dict) and claim_id and claim_id in (e.get("claim_ids") or [])
    ]
    return related[:8]


def _search_counterevidence(
    context: ResearchRunContext,
    claim_id: str,
    claim_text: str,
    tools: Optional[Any],
    llm: Optional[Any]
    ,
    max_counter_queries: int = 3
) -> Dict:
    """反証探索"""
    result = {
        "claim_id": claim_id,
        "search_queries": [],
        "search_scope": "web",
        "found_counterevidence": False,
        "impact_on_status": None,
        "searched_at": datetime.now().isoformat()
    }
    
    if not tools:
        return result
    
    # 反証クエリ生成（複数）
    default_queries = [
        f"{claim_text} 批判",
        f"{claim_text} 反論",
        f"{claim_text} limitation",
        f"{claim_text} debunk",
        f"{claim_text} 問題点",
    ]
    queries = default_queries[:max_counter_queries]
    if llm and hasattr(llm, "generate_counter_queries"):
        try:
            queries = call_tool(
                context,
                tool_name="llm.generate_counter_queries",
                call=lambda: llm.generate_counter_queries(claim_text),
                args={"claim_id": claim_id},
                result_summary=lambda rows: {"queries_count": len(rows or [])},
            )[:max_counter_queries]
        except Exception:
            queries = default_queries[:max_counter_queries]
    elif llm and hasattr(llm, "generate_counter_query"):
        try:
            one_query = call_tool(
                context,
                tool_name="llm.generate_counter_query",
                call=lambda: llm.generate_counter_query(claim_text),
                args={"claim_id": claim_id},
                result_summary=lambda row: {"query_length": len(str(row or ""))},
            )
            queries = [one_query]
        except Exception:
            queries = default_queries[:1]
    
    result["search_queries"] = [q for q in queries if q]
    
    try:
        found_any = False
        total_hits = 0
        for q in result["search_queries"]:
            results = call_tool(
                context,
                tool_name="tools.search_web",
                call=lambda q=q: tools.search_web(q),
                args={"query": q, "mode": "counterevidence"},
                result_summary=lambda rows: {"results_count": len(rows or [])},
            )
            n = len(results) if results else 0
            total_hits += n
            if n:
                found_any = True
        result["found_counterevidence"] = found_any
        # 雑だが、反証の「ありそう度」をstatus影響に変換
        if total_hits >= 6:
            result["impact_on_status"] = "CONTESTED"
    except Exception:
        pass
    
    return result


def _calculate_weights(
    evidence: List[Dict],
    counter_result: Dict
) -> tuple:
    """支持/反証の重みを計算"""
    pos_weight = sum(
        float(e.get("quality_score", 0.5))
        for e in evidence
        if e.get("stance") == "supports"
    )
    neg_weight = sum(
        float(e.get("quality_score", 0.5))
        for e in evidence
        if e.get("stance") == "refutes"
    )
    
    # 反証探索結果を加味
    if counter_result.get("found_counterevidence"):
        neg_weight += 0.5
    
    return pos_weight or 0.5, neg_weight or 0.0


def _is_primary_source(e: Dict) -> bool:
    tier = str(e.get("tier", ""))
    return tier in ("S", "tier1")


def _evidence_ref(e: Dict) -> str:
    return str(e.get("evidence_id") or e.get("source_id") or e.get("url") or "")
