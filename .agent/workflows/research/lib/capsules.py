# -*- coding: utf-8 -*-
"""
Research Capsules

目的:
- `_outputs/research/<session>/audit_pack.json` は重くて横断検索しづらい
- そこで “再利用のための最小要約” を `knowledge/research/capsules.jsonl` に追記する

方針:
- 監査用の原本は audit_pack.json（フル）を参照
- capsules は “探すための索引 + /CODEに渡せる要点” に限定し、サイズ上限を設ける
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # fallback: assume 5 levels up from .agent/workflows/research/lib/*
    return start.resolve().parents[5]


def capsules_path() -> Path:
    root = _find_repo_root(Path(__file__).resolve())
    return root / "knowledge" / "research" / "capsules.jsonl"


def _truncate(s: str, n: int) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def build_capsule(audit_pack: Dict[str, Any]) -> Dict[str, Any]:
    """
    audit_pack（Phase4保存用の監査パック）から capsule を構築。
    """
    now = datetime.now().isoformat()
    query = str(audit_pack.get("query") or "")
    session_id = str(audit_pack.get("session_id") or "")
    summary = audit_pack.get("summary") if isinstance(audit_pack.get("summary"), dict) else {}
    report_data = audit_pack.get("report_data") if isinstance(audit_pack.get("report_data"), dict) else {}

    # Decisions & claim cards（/CODE向けに再利用）
    decisions = _safe_list(report_data.get("decisions"))
    claim_cards = _safe_list(report_data.get("claim_cards"))

    # Cap sizes
    decisions = decisions[:80]
    claim_cards = claim_cards[:30]

    # Sources: URLのみ保持（多すぎるとインデックスが太るのでカット）
    sources = _safe_list(report_data.get("sources"))[:50]

    # Compact claim cards: keep statement/status + evidence refs + short quotes
    compact_cards = []
    for card in claim_cards:
        if not isinstance(card, dict):
            continue
        sup = _safe_list(card.get("supporting_evidence"))
        ref = _safe_list(card.get("refuting_evidence"))

        def compact_ev(ev: Any) -> Optional[Dict[str, Any]]:
            if not isinstance(ev, dict):
                return None
            return {
                "evidence_id": ev.get("evidence_id") or ev.get("ref") or ev.get("source_id") or ev.get("url"),
                "url": ev.get("url") or ev.get("source_id"),
                "locator": ev.get("locator"),
                "quote": _truncate(str(ev.get("quote") or ""), 200),
                "stance": ev.get("stance"),
                "tier": ev.get("tier"),
                "quality_score": ev.get("quality_score"),
            }

        compact_cards.append(
            {
                "claim_id": card.get("claim_id"),
                "statement": _truncate(str(card.get("statement") or ""), 240),
                "status": card.get("status"),
                "action": next((d.get("action") for d in decisions if isinstance(d, dict) and d.get("claim_id") == card.get("claim_id")), None),
                "conditions": card.get("conditions") or [],
                "supporting_evidence": [x for x in (compact_ev(e) for e in sup[:3]) if x],
                "refuting_evidence": [x for x in (compact_ev(e) for e in ref[:3]) if x],
            }
        )

    capsule = {
        "schema_version": 1,
        "created_at": now,
        "session_id": session_id,
        "query": query,
        "summary": summary,
        "decisions": decisions,
        "claim_cards": compact_cards,
        "sources": sources,
        # 原本への参照（パスは環境依存なので、相対名だけ持つ）
        "artifacts": {
            "output_dir": audit_pack.get("output_dir", "") or "",
        },
    }
    return capsule


def append_capsule(capsule: Dict[str, Any]) -> Path:
    """
    `knowledge/research/capsules.jsonl` に追記。
    """
    path = capsules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(capsule, ensure_ascii=False) + "\n")
    return path


def _tokenize(text: str) -> List[str]:
    t = (text or "").lower()
    for ch in "\t\r\n,.;:()[]{}<>\"'":
        t = t.replace(ch, " ")
    tokens = [x for x in t.split(" ") if x]
    # dedup while preserving order
    seen = set()
    out = []
    for tok in tokens:
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def search_capsules(
    *,
    query: str,
    limit: int = 5,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    capsules.jsonl を軽量検索して上位を返す（ローカル・オフライン）。
    スコアは単純なトークン一致＋VERIFIED/REFUTEDの重み付け。
    """
    p = path or capsules_path()
    if not p.exists():
        return []
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    scored: List[tuple[float, Dict[str, Any]]] = []
    with p.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            hay = " ".join(
                [
                    str(obj.get("query") or ""),
                    json.dumps(obj.get("decisions") or [], ensure_ascii=False),
                    json.dumps(obj.get("claim_cards") or [], ensure_ascii=False),
                ]
            ).lower()
            hits = sum(1 for t in q_tokens if t in hay)
            if hits == 0:
                continue

            # reward “actionable” outcomes
            decisions = obj.get("decisions") if isinstance(obj.get("decisions"), list) else []
            bonus = 0.0
            for d in decisions:
                if not isinstance(d, dict):
                    continue
                status = str(d.get("status") or "")
                if status in ("VERIFIED", "REFUTED"):
                    bonus += 0.5
            score = float(hits) + bonus
            scored.append((score, obj))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, obj in scored[: max(1, int(limit))]:
        results.append(
            {
                "score": score,
                "session_id": obj.get("session_id"),
                "query": obj.get("query"),
                "summary": obj.get("summary"),
                "decisions": obj.get("decisions"),
                "claim_cards": obj.get("claim_cards"),
                "sources": obj.get("sources"),
                "artifacts": obj.get("artifacts"),
            }
        )
    return results


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Search knowledge/research/capsules.jsonl")
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    res = search_capsules(query=args.query, limit=args.limit)
    print(json.dumps({"count": len(res), "results": res}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve()
    for _parent in _repo_root.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in sys.path:
                sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("research", "capsules", _cli, phase_name="CAPSULES_CLI"))

