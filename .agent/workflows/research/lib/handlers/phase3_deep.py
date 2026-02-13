# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase 3 Handler (DEEP)
深層調査: Gap/sub_questionsに基づく追加検索 → Evidence収集

LLM責務:
- サブ質問 → 検索クエリ変換
- 本文 → Evidence抽出（quote/stance/quality）

純粋ロジック:
- Evidence → scoring.aggregate_confidence
- RoundSnapshot組み立て
- termination.should_stop判定
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal
from ..termination import should_stop, RoundSnapshot, ClaimSnapshot
from ..scoring import Evidence as ScoringEvidence, aggregate_confidence
from ..models import generate_evidence_id, Stance
from ..locator import build_locator, is_strong_locator
from ..tool_trace import call_tool


def make_deep_handler(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Callable[[ResearchRunContext], PhaseResult]:
    """
    Phase 3 (DEEP) ハンドラを生成
    """
    cfg = config or {}
    max_evidence_per_claim = cfg.get("max_evidence_per_claim", 5)
    # SKILL目安: Phase3は30-50回検索。デフォルトは強めにしてterminationで止める。
    max_rounds = cfg.get("max_deep_rounds", 12)
    max_queries = cfg.get("max_deep_queries_per_round", 10)
    max_results_per_query = cfg.get("max_results_per_query", 5)
    
    def handler(context: ResearchRunContext) -> PhaseResult:
        """Phase 3: 深層調査"""
        sub_questions = context.sub_questions
        normalized_claims = context.normalized_claims
        required_actions = context.required_actions  # 差し戻し時の追加調査項目
        
        # ラウンド番号をインクリメント
        context.deep_round += 1
        current_round = context.deep_round
        
        # 差し戻し時は required_actions を優先
        queries = required_actions if required_actions else sub_questions
        
        if not queries and not normalized_claims:
            return PhaseResult(
                phase=Phase.DEEP,
                success=True,
                signal=PhaseSignal.NEXT,
                output={"evidence_count": 0},
                notes="Phase 3: 調査対象がないためスキップ"
            )
        
        try:
            evidence: List[Dict] = context.evidence.copy()  # 既存を継承
            searches_performed = 0
            
            # 1. サブ質問に基づく追加検索
            if tools:
                for q in queries[:max_queries]:
                    query_text = q.get("query", q) if isinstance(q, dict) else str(q)
                    try:
                        results = call_tool(
                            context,
                            tool_name="tools.search_web",
                            call=lambda query_text=query_text: tools.search_web(query_text),
                            args={"query": query_text},
                            result_summary=lambda rows: {"results_count": len(rows or [])},
                        )
                        searches_performed += 1
                        if results:
                            for r in results[:max_results_per_query]:
                                url = r.get("url", "")
                                content = ""
                                if not url:
                                    continue

                                # ガード: 同run内で既にfetch済みのURLはスキップ
                                if url in context.seen_urls:
                                    continue

                                # ガード: サーキットブレーカーによる遮断チェック
                                breaker = context.circuit_breaker
                                if breaker:
                                    skip, reason = breaker.should_skip(url)
                                    if skip:
                                        # skippedをログに記録
                                        call_tool(
                                            context,
                                            tool_name="tools.read_url_content",
                                            call=lambda: None,
                                            args={"url": url, "skipped": True, "reason": reason},
                                            result_summary=lambda _: {"status": "skipped", "reason": reason},
                                        )
                                        continue

                                try:
                                    content = call_tool(
                                        context,
                                        tool_name="tools.read_url_content",
                                        call=lambda url=url: tools.read_url_content(url),
                                        args={"url": url},
                                        result_summary=lambda text: {"chars": len(text or "")},
                                    )
                                except Exception as fetch_err:
                                    # 失敗をサーキットブレーカーに記録
                                    error_text = str(fetch_err)
                                    error_code = None
                                    if "403" in error_text:
                                        error_code = 403
                                    elif "401" in error_text:
                                        error_code = 401
                                    elif "404" in error_text:
                                        error_code = 404
                                    if breaker:
                                        breaker.record_failure(url, error_code, error_text)
                                    content = ""

                                if content:
                                    context.seen_urls.add(url)
                                    if breaker:
                                        breaker.record_success(url)
                                    # Evidence抽出
                                    extracted = _extract_evidence(
                                        context, content, url, normalized_claims, llm
                                    )
                                    extracted = _coerce_evidence(extracted, url, normalized_claims, content)
                                    # locatorが弱いEvidenceは保存しない（監査可能性優先）
                                    extracted = [e for e in extracted if is_strong_locator(e.get("locator"))]
                                    evidence.extend(extracted[:max_evidence_per_claim])
                    except Exception:
                        pass  # 個別エラーは無視（search_web失敗等）
            
            # 2. coverage_map更新
            coverage_map = _build_coverage_map(normalized_claims, evidence)
            _update_gap_states_in_context(context, coverage_map)
            
            # 3. 終了条件判定
            snapshot = _build_round_snapshot(
                context,
                current_round,
                normalized_claims,
                evidence,
                coverage_map,
                searches_performed=searches_performed
            )
            
            termination_result = should_stop(
                context.termination_state,
                snapshot,
                context.termination_config
            )
            
            # 4. コンテキストに出力を格納
            context.evidence = evidence
            context.coverage_map = coverage_map
            context.required_actions = []  # 差し戻し用をクリア
            
            # 5. 遷移判定
            if termination_result.should_stop or current_round >= max_rounds:
                signal = PhaseSignal.NEXT
                notes = f"Phase 3完了 (R{current_round}): 終了条件到達"
            else:
                signal = PhaseSignal.CONTINUE  # 同Phaseを継続
                notes = f"Phase 3継続 (R{current_round}): coverage={termination_result.coverage:.2f}"
            
            return PhaseResult(
                phase=Phase.DEEP,
                success=True,
                signal=signal,
                output={
                    "round": current_round,
                    "evidence_count": len(evidence),
                    "coverage": termination_result.coverage,
                    "should_stop": termination_result.should_stop,
                    "reason": termination_result.reason
                },
                notes=notes
            )
            
        except Exception as e:
            return PhaseResult(
                phase=Phase.DEEP,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
    
    return handler


def _extract_evidence(
    context: ResearchRunContext,
    content: str,
    url: str,
    claims: List[Dict],
    llm: Optional[Any]
) -> List[Dict]:
    """コンテンツからEvidence抽出"""
    if llm and hasattr(llm, "extract_evidence"):
        return call_tool(
            context,
            tool_name="llm.extract_evidence",
            call=lambda: llm.extract_evidence(content, claims),
            args={
                "url": url,
                "content_chars": len(content),
                "claims_count": len(claims),
            },
            result_summary=lambda rows: {"evidence_count": len(rows or [])},
        )
    
    # スタブ: コンテンツ先頭を引用として
    return [{
        "evidence_id": generate_evidence_id(),
        "claim_ids": [claims[0].get("claim_id")] if claims else [],
        "source_id": url,
        "url": url,
        "quote": content[:300] if len(content) > 300 else content,
        "stance": "supports",
        "quality_score": 0.5,
        "tier": "C",
        "locator": build_locator(url=url, content=content, quote=(content[:300] if len(content) > 300 else content)) or None,
        "extracted_at": datetime.now().isoformat()
    }]

def _coerce_evidence(extracted: Any, url: str, claims: List[Dict], content: str) -> List[Dict]:
    """
    LLM/スタブのEvidence出力をArtifactsスキーマに寄せる。
    必須: evidence_id, claim_ids, quote, stance, quality_score
    """
    if not extracted:
        return []
    if isinstance(extracted, dict):
        extracted = [extracted]
    if not isinstance(extracted, list):
        return []
    
    claim_ids = [c.get("claim_id") for c in claims if c.get("claim_id")]
    primary_claim_id = claim_ids[0] if claim_ids else None
    
    coerced: List[Dict] = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        d = dict(item)
        d.setdefault("evidence_id", generate_evidence_id())
        d.setdefault("source_id", url)
        d.setdefault("url", url)
        
        quote = d.get("quote") or d.get("text") or ""
        d["quote"] = str(quote)
        
        stance: Stance = d.get("stance") or "neutral"
        if stance not in ("supports", "refutes", "neutral"):
            stance = "neutral"
        d["stance"] = stance
        
        try:
            d["quality_score"] = float(d.get("quality_score", 0.5))
        except Exception:
            d["quality_score"] = 0.5
        
        d.setdefault("tier", "C")
        d.setdefault("bias_flags", [])
        d.setdefault("freshness", None)
        loc = d.get("locator") or None
        # locator未指定なら本文から生成を試す
        if not loc and d["quote"]:
            loc = build_locator(url=url, content=content, quote=d["quote"])
        d.setdefault("locator", loc)
        d.setdefault("extracted_at", datetime.now().isoformat())
        
        # claim_ids: LLMが付けていなければ最低1つ紐づける
        cids = d.get("claim_ids") or d.get("claim_id") or []
        if isinstance(cids, str):
            cids = [cids]
        if not isinstance(cids, list):
            cids = []
        cids = [c for c in cids if isinstance(c, str) and c]
        if not cids and primary_claim_id:
            cids = [primary_claim_id]
        d["claim_ids"] = list(dict.fromkeys(cids))
        d.pop("claim_id", None)  # 単数表現は捨てる（保存スキーマを一貫させる）
        
        coerced.append(d)
    
    return coerced


def _build_coverage_map(claims: List[Dict], evidence: List[Dict]) -> Dict:
    """Claim → Evidence のカバレッジマップ"""
    coverage = {}
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        related = [
            e for e in evidence
            if isinstance(e, dict) and claim_id and claim_id in (e.get("claim_ids") or [])
        ]
        citeable = [e for e in related if e.get("quote") and e.get("locator")]
        coverage[claim_id] = {
            "evidence_count": len(related),
            "citeable_count": len(citeable),
            "covered": len(related) > 0
        }
    return coverage

def _update_gap_states_in_context(context: ResearchRunContext, coverage_map: Dict[str, Any]) -> None:
    """
    gaps[].state を Evidence カバレッジに基づいて更新。
    Gapは Phase2で作られ、Phase3で閉じる想定なので、ここで状態を進める。
    """
    if not context.gaps:
        return
    for gap in context.gaps:
        if not isinstance(gap, dict):
            continue
        related = gap.get("related_claims") or []
        if not isinstance(related, list) or not related:
            continue
        covered = all(coverage_map.get(cid, {}).get("covered") for cid in related)
        gap["state"] = "CLOSED" if covered else gap.get("state", "OPEN") or "OPEN"


def _build_round_snapshot(
    context: ResearchRunContext,
    round_idx: int,
    claims: List[Dict],
    evidence: List[Dict],
    coverage_map: Dict,
    searches_performed: int = 1
) -> RoundSnapshot:
    """RoundSnapshotを構築"""
    # Gap状態
    gaps = {
        g.get("gap_id", f"gap_{i}"): g.get("state", "OPEN")
        for i, g in enumerate(context.gaps)
    }
    
    # Claim状態
    claim_snapshots = {}
    for claim in claims:
        claim_id = claim.get("claim_id", "")
        related = [
            e for e in evidence
            if isinstance(e, dict) and claim_id and claim_id in (e.get("claim_ids") or [])
        ]
        scoring_evs: List[ScoringEvidence] = []
        for e in related:
            tier = str(e.get("tier", "C"))
            # 後方互換: tier1/tier2/tier3 → S/B/C
            if tier == "tier1":
                tier = "S"
            elif tier == "tier2":
                tier = "B"
            elif tier == "tier3":
                tier = "C"
            stance = e.get("stance") if e.get("stance") in ("supports", "refutes", "neutral") else "neutral"
            try:
                qs = float(e.get("quality_score", 0.5))
            except Exception:
                qs = 0.5
            scoring_evs.append(ScoringEvidence(
                claim_id=claim_id,
                url=str(e.get("url", e.get("source_id", ""))),
                tier=tier,
                published_at=None,
                stance=stance,
                bias_flags=set(e.get("bias_flags") or []),
                citations_to_high_tier=0,
                cluster_key=str(e.get("cluster_key", "")) if e.get("cluster_key") else ""
            ))
        
        conf = 0.0
        evidence_mass = 0.0
        if scoring_evs:
            conf_res = aggregate_confidence(scoring_evs, now=datetime.now(), time_sensitivity="MEDIUM")
            conf = conf_res.confidence
            evidence_mass = conf_res.evidence_mass
        
        has_primary = any(str(e.get("tier")) in ("S", "tier1") for e in related)
        # 簡易: 一次ソースがあるほどtelephoneリスクは下がる
        telephone_risk = 0.25 if has_primary else 0.6
        claim_snapshots[claim_id] = ClaimSnapshot(
            claim_id=claim_id,
            status="UNSUPPORTED",  # デフォルト
            confidence=conf,
            evidence_mass=evidence_mass,
            telephone_risk=telephone_risk
        )
    
    return RoundSnapshot(
        round_idx=round_idx,
        gaps=gaps,
        claims=claim_snapshots,
        cost=max(1.0, float(searches_performed)),
        budget_used=float(round_idx) * max(1.0, float(searches_performed)),
        budget_limit=context.termination_config.hard_cap_budget,
        hard_cap_rounds=10
    )
