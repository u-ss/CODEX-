# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase 4 Handler (INTEGRATE)
統合: 検証済みClaimから最終レポート生成

LLM責務:
- レポート文章生成

純粋ロジック:
- 引用URL整形
- 未検証Claimの警告付け
- レポート構造化
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal
from ..tool_trace import call_tool


def make_integrate_handler(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Callable[[ResearchRunContext], PhaseResult]:
    """
    Phase 4 (INTEGRATE) ハンドラを生成
    """
    cfg = config or {}
    include_unverified = cfg.get("include_unverified_claims", True)
    
    def handler(context: ResearchRunContext) -> PhaseResult:
        """Phase 4: 統合"""
        verified_claims = context.verified_claims
        normalized_claims = context.normalized_claims
        evidence = context.evidence
        
        try:
            # 1. レポート構造を構築
            report_data = _build_report_structure(
                query=context.query,
                verified_claims=verified_claims,
                normalized_claims=normalized_claims,
                evidence=evidence,
                include_unverified=include_unverified
            )
            
            # 2. LLMでレポート文章生成（または構造化テキスト）
            if llm and hasattr(llm, "generate_report"):
                report_text = call_tool(
                    context,
                    tool_name="llm.generate_report",
                    call=lambda: llm.generate_report(report_data),
                    args={
                        "query": context.query,
                        "verified_claims_count": len(verified_claims),
                    },
                    result_summary=lambda text: {"report_chars": len(text or "")},
                )
            else:
                report_text = _generate_structured_report(report_data)
            
            # 3. コンテキストに出力を格納
            context.final_report = report_text
            context.report_data = report_data
            
            return PhaseResult(
                phase=Phase.INTEGRATE,
                success=True,
                signal=PhaseSignal.NEXT,
                output={
                    "report_length": len(report_text),
                    "claims_in_report": len(verified_claims),
                    "sources_cited": len(set(e.get("source_id", "") for e in evidence))
                },
                notes=f"Phase 4完了: レポート生成（{len(report_text)}文字）"
            )
            
        except Exception as e:
            return PhaseResult(
                phase=Phase.INTEGRATE,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
    
    return handler


def _build_report_structure(
    query: str,
    verified_claims: List[Dict],
    normalized_claims: List[Dict],
    evidence: List[Dict],
    include_unverified: bool
) -> Dict:
    """レポート構造を構築"""
    # 検証済みClaimをステータス別に分類
    by_status = {}
    verified_ids = set()
    
    for vc in verified_claims:
        status = vc.get("status", "UNSUPPORTED")
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(vc)
        verified_ids.add(vc.get("claim_id"))
    
    # 未検証Claim
    unverified = []
    if include_unverified:
        unverified = [
            nc for nc in normalized_claims
            if nc.get("claim_id") not in verified_ids
        ]
    
    # 引用URL一覧
    sources = list(set(
        e.get("url", e.get("source_id", ""))
        for e in evidence
        if e.get("url") or e.get("source_id")
    ))

    evidence_by_ref = {}
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        ref = str(ev.get("evidence_id") or ev.get("source_id") or ev.get("url") or "")
        if ref:
            evidence_by_ref[ref] = ev

    claim_cards = []
    decisions = []
    for vc in verified_claims:
        cid = vc.get("claim_id")
        status = vc.get("status", "UNSUPPORTED")
        sup = vc.get("supporting_evidence_ids") or []
        ref = vc.get("refuting_evidence_ids") or []
        card = {
            "claim_id": cid,
            "statement": vc.get("statement"),
            "status": status,
            "rationale": vc.get("rationale"),
            "conditions": vc.get("conditions") or [],
            "supporting_evidence": [evidence_by_ref.get(str(x), {"ref": str(x)}) for x in sup],
            "refuting_evidence": [evidence_by_ref.get(str(x), {"ref": str(x)}) for x in ref],
        }
        claim_cards.append(card)

        if status in ("VERIFIED", "CONDITIONED"):
            action = "use"
        elif status == "REFUTED":
            action = "avoid"
        else:
            action = "investigate"
        decisions.append({
            "claim_id": cid,
            "action": action,
            "status": status,
            "notes": vc.get("conditions") if status == "CONDITIONED" else [],
        })
    
    return {
        "query": query,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_claims": len(normalized_claims),
            "verified_claims": len(verified_claims),
            "unverified_claims": len(unverified),
            "sources_count": len(sources)
        },
        "claims_by_status": by_status,
        "claim_cards": claim_cards,
        "decisions": decisions,
        "unverified_claims": unverified,
        "sources": sources
    }


def _generate_structured_report(data: Dict) -> str:
    """構造化レポートを生成（LLMなし版）"""
    lines = []
    
    # ヘッダー
    lines.append(f"# リサーチレポート: {data['query']}")
    lines.append(f"\n生成日時: {data['generated_at']}")
    lines.append("")
    
    # サマリ
    summary = data["summary"]
    lines.append("## 概要")
    lines.append(f"- 抽出Claim数: {summary['total_claims']}")
    lines.append(f"- 検証済み: {summary['verified_claims']}")
    lines.append(f"- 未検証: {summary['unverified_claims']}")
    lines.append(f"- 参照ソース数: {summary['sources_count']}")
    lines.append("")
    
    # ステータス別Claim
    status_labels = {
        "VERIFIED": "✅ 検証済み",
        "CONDITIONED": "⚠️ 条件付き",
        "CONTESTED": "⚔️ 論争中",
        "UNSUPPORTED": "❓ 根拠不足",
        "REFUTED": "❌ 否定"
    }
    
    for status, claims in data["claims_by_status"].items():
        label = status_labels.get(status, status)
        lines.append(f"## {label}")
        for claim in claims:
            statement = claim.get("statement", "")
            rationale = claim.get("rationale", "")
            lines.append(f"- **{statement}**")
            if rationale:
                lines.append(f"  - 根拠: {rationale}")
        lines.append("")
    
    # 未検証Claim（警告付き）
    if data["unverified_claims"]:
        lines.append("## ⚠️ 未検証Claim")
        lines.append("> 以下のClaimは十分な検証が行われていません。")
        lines.append("")
        for claim in data["unverified_claims"][:5]:
            lines.append(f"- {claim.get('statement', '')}")
        lines.append("")
    
    # 参照ソース
    lines.append("## 参照ソース")
    for i, source in enumerate(data["sources"][:20], 1):
        lines.append(f"{i}. {source}")
    
    return "\n".join(lines)
