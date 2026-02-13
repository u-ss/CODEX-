# -*- coding: utf-8 -*-
"""
Research Agent - Quality Gate

Antigravity（LLM）が推論・抽出する前提でも、Artifactsのスキーマと最低限の検証手順が
毎回満たされるように「機械的に落とす」ための軽量ゲート。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .locator import is_strong_locator


def validate_phase35(
    *,
    verified_claims: List[Dict[str, Any]],
    counterevidence_log: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    """
    Phase 3.5の出力を検査。

    Returns:
        (errors, warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []

    evidence_by_ref: Dict[str, Dict[str, Any]] = {}
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        ref = str(ev.get("evidence_id") or ev.get("source_id") or ev.get("url") or "")
        if ref:
            evidence_by_ref[ref] = ev

    vc_by_id = {}
    for vc in verified_claims:
        if not isinstance(vc, dict):
            errors.append("verified_claims item is not a dict")
            continue
        cid = str(vc.get("claim_id") or "")
        if not cid:
            errors.append("verified_claims missing claim_id")
            continue
        vc_by_id[cid] = vc

        if not (vc.get("statement") or "").strip():
            errors.append(f"verified_claims[{cid}] missing statement")
        if not (vc.get("status") or "").strip():
            errors.append(f"verified_claims[{cid}] missing status")
        if not (vc.get("rationale") or "").strip():
            warnings.append(f"verified_claims[{cid}] missing rationale")

        # 強いステータスの最低条件（SKILLの目安に寄せる）
        status = str(vc.get("status"))
        sup = vc.get("supporting_evidence_ids") or []
        ref = vc.get("refuting_evidence_ids") or []
        if status == "VERIFIED" and len(sup) < 2:
            warnings.append(f"verified_claims[{cid}] VERIFIED but supporting_evidence_ids < 2")
        if status == "REFUTED" and len(ref) < 2:
            warnings.append(f"verified_claims[{cid}] REFUTED but refuting_evidence_ids < 2")
        # 引用可能性: 参照しているEvidenceは locator + quote があること
        for label, refs in [("supporting_evidence_ids", sup), ("refuting_evidence_ids", ref)]:
            if not isinstance(refs, list):
                warnings.append(f"verified_claims[{cid}] {label} is not a list")
                continue
            for r in refs:
                r = str(r or "")
                if not r:
                    continue
                ev = evidence_by_ref.get(r)
                if not ev:
                    errors.append(f"verified_claims[{cid}] evidence ref not found: {r}")
                    continue
                if not (ev.get("quote") or "").strip():
                    errors.append(f"evidence[{r}] missing quote")
                if not is_strong_locator(ev.get("locator")):
                    errors.append(f"evidence[{r}] missing/weak locator")

    # counterevidence_log: 主要Claim（= verified_claimsに出たClaim）にはログ必須
    ce_by_id = {}
    for ce in counterevidence_log:
        if not isinstance(ce, dict):
            errors.append("counterevidence_log item is not a dict")
            continue
        cid = str(ce.get("claim_id") or "")
        if not cid:
            errors.append("counterevidence_log missing claim_id")
            continue
        ce_by_id[cid] = ce
        queries = ce.get("search_queries") or []
        if not isinstance(queries, list) or not [q for q in queries if str(q).strip()]:
            warnings.append(f"counterevidence_log[{cid}] missing search_queries")
        elif len([q for q in queries if str(q).strip()]) < 2:
            warnings.append(f"counterevidence_log[{cid}] search_queries < 2")

    for cid in vc_by_id.keys():
        if cid not in ce_by_id:
            errors.append(f"missing counterevidence_log for claim_id={cid}")

    # Evidence: citeable要件（quote + locator）を満たす比率が低すぎる場合は警告
    if evidence:
        citeable = 0
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            if (ev.get("quote") or "").strip() and (ev.get("locator") or "").strip():
                citeable += 1
        ratio = citeable / max(1, len(evidence))
        if ratio < 0.5:
            warnings.append(f"low citeable evidence ratio: {citeable}/{len(evidence)}")

    return errors, warnings
