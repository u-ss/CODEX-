#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stealth Local Research Agent v1.0.0

既存 /research を変更せず、以下を新規パイプラインとして実行する:
- 収集: Stealth Research Tool v2.1
- 推論: Ollama (qwen3:14b / gpt-oss:20b)
- 出力: final_report.md / search_log.md / audit_pack.json ほか
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


DEFAULT_FAST_MODEL = "qwen3:14b"
DEFAULT_ACCURATE_MODEL = "gpt-oss:20b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OUTPUT_ROOT = Path("_outputs/research_stealth_local")
STATUS_VALUES = {"VERIFIED", "CONDITIONED", "CONTESTED", "UNSUPPORTED", "REFUTED"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_runtime_import_paths(root: Path) -> None:
    research_root = root / ".agent" / "workflows" / "research"
    shared_root = root / ".agent" / "workflows" / "shared"
    for path in (research_root, shared_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(text: str, limit: int = 48) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if not value:
        value = "research"
    return value[:limit]


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def clean_html_text(content: str, max_chars: int = 7000) -> str:
    text = content or ""
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = compact_text(text)
    return text[:max_chars]


def hash_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def extract_json_value(text: str) -> Optional[Any]:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    for chunk in fenced:
        try:
            return json.loads(chunk)
        except Exception:
            continue

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", text):
        start = match.start()
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
        except Exception:
            continue
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
    return rows


class OllamaClient:
    def __init__(self, base_url: str, timeout_sec: int):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def generate(self, model: str, prompt: str, temperature: float = 0.2) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        resp = requests.post(url, json=payload, timeout=self.timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "") or "")

    def generate_json(self, model: str, prompt: str, temperature: float = 0.2) -> Optional[Any]:
        try:
            raw = self.generate(model=model, prompt=prompt, temperature=temperature)
        except Exception:
            return None
        return extract_json_value(raw)


@dataclass
class StealthCollection:
    run_id: str
    output_dir: Path
    queries: List[str]
    fetched_contents: List[Dict[str, Any]]
    summary: Dict[str, Any]
    trace_events: List[Dict[str, Any]]


@dataclass
class JobResult:
    goal: str
    focus: str
    status: str
    output_dir: str
    report_path: str
    error: str = ""
    summary: Optional[Dict[str, Any]] = None


def default_queries(goal: str, focus: str, max_queries: int) -> List[str]:
    focus_text = focus.strip()
    if focus_text:
        base = [
            f"{goal} {focus_text} 最新動向",
            f"{goal} {focus_text} 実装事例",
            f"{goal} {focus_text} リスク",
            f"{goal} {focus_text} 比較",
            f"{goal} {focus_text} 制約",
            f"{goal} {focus_text} 失敗例",
        ]
    else:
        base = [
            f"{goal} 最新動向",
            f"{goal} 実装事例",
            f"{goal} リスク",
            f"{goal} 比較",
            f"{goal} 制約",
            f"{goal} 失敗例",
        ]
    return base[:max_queries]


def phase_query_plan(
    client: OllamaClient,
    goal: str,
    focus: str,
    fast_model: str,
    max_queries: int,
) -> List[str]:
    prompt = f"""
あなたはリサーチ設計担当です。
目的に対してWeb検索クエリを作ってください。

goal: {goal}
focus: {focus or "(なし)"}
max_queries: {max_queries}

制約:
- 日本語中心
- 具体語で短く
- 重複しない
- 出力はJSONのみ

JSON形式:
{{
  "queries": ["query1", "query2", "..."]
}}
"""
    obj = client.generate_json(model=fast_model, prompt=prompt, temperature=0.2)
    if isinstance(obj, dict):
        raw = obj.get("queries", [])
        if isinstance(raw, list):
            deduped: List[str] = []
            seen = set()
            for item in raw:
                text = compact_text(str(item))
                if not text:
                    continue
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(text)
                if len(deduped) >= max_queries:
                    break
            if deduped:
                return deduped
    return default_queries(goal, focus, max_queries)


def phase_stealth_collect(
    root: Path,
    queries: List[str],
    output_dir: Path,
    max_urls: int,
    max_fetches: int,
    max_time_sec: float,
    max_results_per_provider: int,
) -> StealthCollection:
    ensure_runtime_import_paths(root)
    from stealth_research.config import StealthResearchConfig  # type: ignore
    from stealth_research.orchestrator import Orchestrator  # type: ignore

    stealth_output = output_dir / "stealth_run"
    stealth_output.mkdir(parents=True, exist_ok=True)

    cfg = StealthResearchConfig()
    cfg.budget.max_urls = max_urls
    cfg.budget.max_fetches = max_fetches
    cfg.budget.max_time_sec = max_time_sec
    cfg.search.max_results_per_provider = max_results_per_provider
    cfg.log.artifact_dir = str(stealth_output)

    orchestrator = Orchestrator(cfg)
    try:
        result = orchestrator.run(queries, str(stealth_output))
    finally:
        orchestrator.close()

    trace_path = Path(result.output_dir) / f"{result.run_id}_trace.jsonl"
    trace_events = read_jsonl(trace_path)
    fetched_contents = list(result.fetched_contents or [])
    summary = dict(result.summary or {})
    return StealthCollection(
        run_id=str(result.run_id),
        output_dir=Path(result.output_dir),
        queries=list(result.queries or queries),
        fetched_contents=fetched_contents,
        summary=summary,
        trace_events=trace_events,
    )


def _coerce_stance(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"supports", "support"}:
        return "supports"
    if v in {"refutes", "refute"}:
        return "refutes"
    return "neutral"


def _coerce_relevance(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"high", "medium", "low"}:
        return v
    return "medium"


def phase_extract_claims(
    client: OllamaClient,
    goal: str,
    focus: str,
    fast_model: str,
    fetched_contents: List[Dict[str, Any]],
    max_claims: int,
    max_chars_per_doc: int,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw_claims: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []

    for doc in fetched_contents:
        if len(raw_claims) >= max_claims:
            break
        url = str(doc.get("url", "") or "")
        content = str(doc.get("content", "") or "")
        text = clean_html_text(content, max_chars=max_chars_per_doc)
        if not url or not text:
            continue

        prompt = f"""
あなたは調査結果のClaim抽出器です。本文から検証可能な主張を抽出してください。

goal: {goal}
focus: {focus or "(なし)"}
source_url: {url}
max_claims: 5

本文:
\"\"\"{text}\"\"\"

出力はJSONのみ:
{{
  "claims": [
    {{
      "statement": "...",
      "quote": "...",
      "stance": "supports|refutes|neutral",
      "quality_score": 0.0-1.0,
      "decision_relevance": "high|medium|low"
    }}
  ]
}}
"""
        obj = client.generate_json(model=fast_model, prompt=prompt, temperature=0.2)
        parsed_claims: List[Dict[str, Any]] = []
        if isinstance(obj, dict) and isinstance(obj.get("claims"), list):
            parsed_claims = [x for x in obj["claims"] if isinstance(x, dict)]

        if not parsed_claims:
            fallback_statement = compact_text(text[:180])
            if fallback_statement:
                parsed_claims = [{
                    "statement": fallback_statement,
                    "quote": fallback_statement,
                    "stance": "neutral",
                    "quality_score": 0.45,
                    "decision_relevance": "medium",
                }]

        for claim in parsed_claims:
            if len(raw_claims) >= max_claims:
                break
            statement = compact_text(str(claim.get("statement", "")))
            quote = compact_text(str(claim.get("quote", "")))[:320]
            if not statement:
                continue
            claim_id = hash_id("rawclm", statement, url)
            evidence_id = hash_id("evd", url, quote or statement)
            stance = _coerce_stance(str(claim.get("stance", "")))
            try:
                quality_score = float(claim.get("quality_score", 0.55))
            except Exception:
                quality_score = 0.55
            quality_score = max(0.0, min(1.0, quality_score))
            relevance = _coerce_relevance(str(claim.get("decision_relevance", "medium")))

            raw_claims.append({
                "claim_id": claim_id,
                "statement": statement,
                "source_url": url,
                "quote": quote,
                "decision_relevance": relevance,
                "fingerprint": hash_id("fp", statement),
                "extracted_at": now_iso(),
                "source_evidence_id": evidence_id,
            })
            evidence.append({
                "evidence_id": evidence_id,
                "claim_ids": [claim_id],
                "source_id": url,
                "url": url,
                "quote": quote or statement[:300],
                "stance": stance,
                "quality_score": quality_score,
                "tier": "C",
                "locator": url,
                "extracted_at": now_iso(),
            })

    return raw_claims, evidence


def phase_normalize_claims(
    client: OllamaClient,
    goal: str,
    focus: str,
    fast_model: str,
    raw_claims: List[Dict[str, Any]],
    max_claims: int,
) -> List[Dict[str, Any]]:
    if not raw_claims:
        return []

    claim_rows = [
        {"claim_id": c["claim_id"], "statement": c["statement"], "source_url": c["source_url"]}
        for c in raw_claims[:max_claims * 2]
    ]
    prompt = f"""
あなたはClaim正規化器です。類似主張を統合し、意思決定用に整理してください。

goal: {goal}
focus: {focus or "(なし)"}
max_claims: {max_claims}

入力Claim:
{json.dumps(claim_rows, ensure_ascii=False)}

出力JSON:
{{
  "normalized_claims": [
    {{
      "statement": "...",
      "scope": "...",
      "decision_relevance": "high|medium|low",
      "source_claim_ids": ["rawclm_xxx"]
    }}
  ]
}}
"""
    obj = client.generate_json(model=fast_model, prompt=prompt, temperature=0.2)
    normalized: List[Dict[str, Any]] = []
    raw_by_id = {c["claim_id"]: c for c in raw_claims}

    if isinstance(obj, dict) and isinstance(obj.get("normalized_claims"), list):
        for row in obj["normalized_claims"]:
            if not isinstance(row, dict):
                continue
            statement = compact_text(str(row.get("statement", "")))
            if not statement:
                continue
            source_claim_ids = row.get("source_claim_ids", [])
            if not isinstance(source_claim_ids, list):
                source_claim_ids = []
            source_claim_ids = [str(x) for x in source_claim_ids if str(x) in raw_by_id]
            source_urls = sorted({
                raw_by_id[cid]["source_url"] for cid in source_claim_ids if cid in raw_by_id
            })
            normalized.append({
                "claim_id": hash_id("nclm", statement),
                "statement": statement,
                "scope": compact_text(str(row.get("scope", "general"))) or "general",
                "decision_relevance": _coerce_relevance(str(row.get("decision_relevance", "medium"))),
                "source_claim_ids": source_claim_ids,
                "source_urls": source_urls,
                "created_at": now_iso(),
            })
            if len(normalized) >= max_claims:
                break

    if not normalized:
        seen = set()
        for row in raw_claims:
            statement = compact_text(str(row.get("statement", "")))
            key = statement.lower()
            if not statement or key in seen:
                continue
            seen.add(key)
            normalized.append({
                "claim_id": hash_id("nclm", statement),
                "statement": statement,
                "scope": "general",
                "decision_relevance": _coerce_relevance(str(row.get("decision_relevance", "medium"))),
                "source_claim_ids": [row["claim_id"]],
                "source_urls": [row["source_url"]],
                "created_at": now_iso(),
            })
            if len(normalized) >= max_claims:
                break

    return normalized


def rank_evidence_for_claim(
    statement: str,
    evidence: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    tokens = set(re.findall(r"[0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]{2,}", statement.lower()))
    scored: List[tuple[float, Dict[str, Any]]] = []
    for ev in evidence:
        quote = str(ev.get("quote", "")).lower()
        overlap = 0
        for tok in tokens:
            if tok in quote:
                overlap += 1
        quality = float(ev.get("quality_score", 0.5) or 0.5)
        score = overlap * 1.5 + quality
        scored.append((score, ev))
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [item[1] for item in scored if item[0] > 0]
    if not ranked:
        ranked = evidence[:top_k]
    return ranked[:top_k]


def _fallback_status(selected_evidence: List[Dict[str, Any]]) -> str:
    supports = sum(1 for ev in selected_evidence if ev.get("stance") == "supports")
    refutes = sum(1 for ev in selected_evidence if ev.get("stance") == "refutes")
    if refutes >= 2 and supports == 0:
        return "REFUTED"
    if supports >= 2 and refutes == 0:
        return "VERIFIED"
    if supports >= 1 and refutes >= 1:
        return "CONTESTED"
    if supports >= 1:
        return "CONDITIONED"
    return "UNSUPPORTED"


def phase_verify_claims(
    client: OllamaClient,
    accurate_model: str,
    normalized_claims: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    max_evidence_per_claim: int,
) -> List[Dict[str, Any]]:
    verified: List[Dict[str, Any]] = []
    for claim in normalized_claims:
        statement = claim["statement"]
        selected = rank_evidence_for_claim(statement, evidence, top_k=max_evidence_per_claim)
        evidence_view = [
            {
                "index": i,
                "evidence_id": ev.get("evidence_id"),
                "url": ev.get("url"),
                "quote": str(ev.get("quote", ""))[:220],
                "stance": ev.get("stance"),
                "quality_score": ev.get("quality_score"),
            }
            for i, ev in enumerate(selected)
        ]
        prompt = f"""
以下のclaimを証拠に基づいて判定してください。

claim: {statement}
候補証拠:
{json.dumps(evidence_view, ensure_ascii=False)}

判定ステータス:
- VERIFIED
- CONDITIONED
- CONTESTED
- UNSUPPORTED
- REFUTED

出力はJSONのみ:
{{
  "status": "...",
  "rationale": "...",
  "conditions": ["..."],
  "supporting_indexes": [0, 2],
  "refuting_indexes": [1]
}}
"""
        obj = client.generate_json(model=accurate_model, prompt=prompt, temperature=0.1)
        status = ""
        rationale = ""
        conditions: List[str] = []
        supporting_ids: List[str] = []
        refuting_ids: List[str] = []

        if isinstance(obj, dict):
            status = str(obj.get("status", "")).upper().strip()
            rationale = compact_text(str(obj.get("rationale", "")))
            conds = obj.get("conditions", [])
            if isinstance(conds, list):
                conditions = [compact_text(str(x)) for x in conds if compact_text(str(x))]
            sup_idx = obj.get("supporting_indexes", [])
            ref_idx = obj.get("refuting_indexes", [])
            if isinstance(sup_idx, list):
                for i in sup_idx:
                    if isinstance(i, int) and 0 <= i < len(selected):
                        supporting_ids.append(str(selected[i].get("evidence_id", "")))
            if isinstance(ref_idx, list):
                for i in ref_idx:
                    if isinstance(i, int) and 0 <= i < len(selected):
                        refuting_ids.append(str(selected[i].get("evidence_id", "")))

        if status not in STATUS_VALUES:
            status = _fallback_status(selected)
        if not rationale:
            rationale = f"evidence={len(selected)}件に基づく判定"

        verified.append({
            "claim_id": claim["claim_id"],
            "statement": statement,
            "status": status,
            "rationale": rationale,
            "conditions": conditions,
            "supporting_evidence_ids": [x for x in supporting_ids if x],
            "refuting_evidence_ids": [x for x in refuting_ids if x],
            "verified_at": now_iso(),
        })
    return verified


def build_report_fallback(
    goal: str,
    focus: str,
    query_plan: List[str],
    verified_claims: List[Dict[str, Any]],
    collection_summary: Dict[str, Any],
) -> str:
    by_status: Dict[str, List[Dict[str, Any]]] = {}
    for row in verified_claims:
        by_status.setdefault(row["status"], []).append(row)

    lines: List[str] = []
    lines.append(f"# ステルス自動リサーチレポート: {goal}")
    lines.append("")
    lines.append(f"- 生成日時: {now_iso()}")
    lines.append(f"- focus: {focus or '(なし)'}")
    lines.append(f"- 実行クエリ数: {len(query_plan)}")
    lines.append(f"- フェッチ成功: {collection_summary.get('successful_fetches', 0)}")
    lines.append("")
    lines.append("## クエリ計画")
    for i, q in enumerate(query_plan, 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    status_order = ["VERIFIED", "CONDITIONED", "CONTESTED", "UNSUPPORTED", "REFUTED"]
    for status in status_order:
        rows = by_status.get(status, [])
        if not rows:
            continue
        lines.append(f"## {status} ({len(rows)}件)")
        for row in rows:
            lines.append(f"- **{row['statement']}**")
            lines.append(f"  - 根拠: {row['rationale']}")
            if row.get("conditions"):
                lines.append(f"  - 条件: {'; '.join(row['conditions'])}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def phase_generate_report(
    client: OllamaClient,
    accurate_model: str,
    goal: str,
    focus: str,
    query_plan: List[str],
    verified_claims: List[Dict[str, Any]],
    collection_summary: Dict[str, Any],
) -> str:
    context = {
        "goal": goal,
        "focus": focus,
        "query_plan": query_plan,
        "collection_summary": collection_summary,
        "verified_claims": verified_claims[:25],
    }
    prompt = f"""
あなたは日本語の調査レポーターです。
以下のデータからMarkdownの最終レポートを生成してください。

- 必須セクション:
  1. 概要
  2. 判定結果（ステータス別）
  3. 推奨アクション
  4. リスクと未解決点

入力:
{json.dumps(context, ensure_ascii=False)}
"""
    try:
        text = client.generate(model=accurate_model, prompt=prompt, temperature=0.15)
        text = text.strip()
        if text:
            return text + ("\n" if not text.endswith("\n") else "")
    except Exception:
        pass
    return build_report_fallback(goal, focus, query_plan, verified_claims, collection_summary)


def build_search_log_md(
    run_id: str,
    queries: List[str],
    trace_events: List[Dict[str, Any]],
    collection_summary: Dict[str, Any],
) -> str:
    query_stats: Dict[str, Dict[str, Any]] = {
        q: {"results": 0, "providers": set()} for q in queries
    }
    selected_urls = 0
    skipped_urls = 0
    fetch_success = 0
    fetch_failed = 0

    for ev in trace_events:
        tool_name = str(ev.get("tool_name", ""))
        event_type = str(ev.get("event_type", ""))
        status = str(ev.get("status", ""))

        if tool_name == "search_web" and event_type == "TOOL_RESULT":
            query = compact_text(str(ev.get("query", "")))
            if query not in query_stats:
                query_stats[query] = {"results": 0, "providers": set()}
            try:
                query_stats[query]["results"] += int(ev.get("results_count", 0))
            except Exception:
                pass
            provider = compact_text(str(ev.get("provider", "")))
            if provider:
                query_stats[query]["providers"].add(provider)

        if tool_name == "url_selection":
            if status == "success" and ev.get("selection_reason") == "selected":
                selected_urls += 1
            elif status == "skipped":
                skipped_urls += 1

        if tool_name == "fetch_url" and event_type == "TOOL_RESULT":
            if status == "success":
                fetch_success += 1
            elif status == "failed":
                fetch_failed += 1

    lines: List[str] = []
    lines.append("# 検索ログ")
    lines.append(f"> run_id: `{run_id}` | generated_at: {now_iso()}")
    lines.append("")
    lines.append("## 検索サマリー")
    lines.append("| # | Query | Results | Providers |")
    lines.append("|:-:|:------|--------:|:----------|")
    for i, q in enumerate(queries, 1):
        stats = query_stats.get(q, {"results": 0, "providers": set()})
        providers = sorted(list(stats["providers"])) if stats["providers"] else ["-"]
        lines.append(f"| {i} | {q} | {stats['results']} | {', '.join(providers)} |")
    lines.append("")
    lines.append("## 統計")
    lines.append(f"- クエリ数: {len(queries)}")
    lines.append(f"- 選定URL数: {selected_urls}")
    lines.append(f"- スキップURL数: {skipped_urls}")
    lines.append(f"- フェッチ成功: {fetch_success}")
    lines.append(f"- フェッチ失敗: {fetch_failed}")
    lines.append(f"- claimed_success: {collection_summary.get('claimed_success', False)}")
    return "\n".join(lines).strip() + "\n"


def ensure_job_output_dir(goal: str, output_dir: str = "") -> Path:
    if output_dir:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out
    stamp = now_stamp()
    slug = slugify(goal, limit=32)
    out = (repo_root() / DEFAULT_OUTPUT_ROOT / f"{stamp}_{slug}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def execute_single_job(job: Dict[str, Any], args: argparse.Namespace) -> JobResult:
    goal = compact_text(str(job.get("goal", "") or args.goal))
    focus = compact_text(str(job.get("focus", "") or args.focus))
    output_dir_arg = str(job.get("output_dir", "") or args.output_dir)
    if not goal:
        raise ValueError("goal is required")

    output_dir = ensure_job_output_dir(goal, output_dir_arg)
    client = OllamaClient(base_url=args.ollama_base_url, timeout_sec=args.ollama_timeout)

    query_plan = phase_query_plan(
        client=client,
        goal=goal,
        focus=focus,
        fast_model=args.fast_model,
        max_queries=args.max_queries,
    )

    collection = phase_stealth_collect(
        root=repo_root(),
        queries=query_plan,
        output_dir=output_dir,
        max_urls=args.max_urls,
        max_fetches=args.max_fetches,
        max_time_sec=args.max_time,
        max_results_per_provider=args.max_results_per_provider,
    )

    raw_claims, evidence = phase_extract_claims(
        client=client,
        goal=goal,
        focus=focus,
        fast_model=args.fast_model,
        fetched_contents=collection.fetched_contents,
        max_claims=args.max_claims,
        max_chars_per_doc=args.max_doc_chars,
    )
    normalized_claims = phase_normalize_claims(
        client=client,
        goal=goal,
        focus=focus,
        fast_model=args.fast_model,
        raw_claims=raw_claims,
        max_claims=args.max_claims,
    )
    verified_claims = phase_verify_claims(
        client=client,
        accurate_model=args.accurate_model,
        normalized_claims=normalized_claims,
        evidence=evidence,
        max_evidence_per_claim=args.max_evidence_per_claim,
    )

    final_report = phase_generate_report(
        client=client,
        accurate_model=args.accurate_model,
        goal=goal,
        focus=focus,
        query_plan=query_plan,
        verified_claims=verified_claims,
        collection_summary=collection.summary,
    )
    search_log = build_search_log_md(
        run_id=collection.run_id,
        queries=query_plan,
        trace_events=collection.trace_events,
        collection_summary=collection.summary,
    )

    report_path = output_dir / "final_report.md"
    search_log_path = output_dir / "search_log.md"
    report_path.write_text(final_report, encoding="utf-8")
    search_log_path.write_text(search_log, encoding="utf-8")

    write_jsonl(output_dir / "raw_claims.jsonl", raw_claims)
    write_jsonl(output_dir / "normalized_claims.jsonl", normalized_claims)
    write_jsonl(output_dir / "evidence.jsonl", evidence)
    write_jsonl(output_dir / "verified_claims.jsonl", verified_claims)

    summary = {
        "goal": goal,
        "focus": focus,
        "generated_at": now_iso(),
        "output_dir": str(output_dir),
        "query_count": len(query_plan),
        "fetched_count": len(collection.fetched_contents),
        "raw_claims_count": len(raw_claims),
        "normalized_claims_count": len(normalized_claims),
        "evidence_count": len(evidence),
        "verified_claims_count": len(verified_claims),
        "stealth_run_id": collection.run_id,
        "stealth_summary": collection.summary,
    }
    write_json(output_dir / "run_summary.json", summary)

    audit_pack = {
        "meta": {
            "agent": "research_stealth_local",
            "version": "1.0.0",
            "generated_at": now_iso(),
        },
        "input": {
            "goal": goal,
            "focus": focus,
            "models": {
                "fast": args.fast_model,
                "accurate": args.accurate_model,
            },
            "ollama_base_url": args.ollama_base_url,
        },
        "query_plan": query_plan,
        "collection": {
            "run_id": collection.run_id,
            "output_dir": str(collection.output_dir),
            "summary": collection.summary,
            "fetched_count": len(collection.fetched_contents),
        },
        "artifacts": {
            "raw_claims_count": len(raw_claims),
            "normalized_claims_count": len(normalized_claims),
            "evidence_count": len(evidence),
            "verified_claims_count": len(verified_claims),
            "final_report_path": str(report_path),
            "search_log_path": str(search_log_path),
        },
        "verified_claims": verified_claims,
    }
    write_json(output_dir / "audit_pack.json", audit_pack)

    return JobResult(
        goal=goal,
        focus=focus,
        status="success",
        output_dir=str(output_dir),
        report_path=str(report_path),
        summary=summary,
    )


def load_jobs_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"jobs file not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        jobs: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                jobs.append(obj)
        return jobs

    obj = json.loads(text)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict) and isinstance(obj.get("jobs"), list):
        return [x for x in obj["jobs"] if isinstance(x, dict)]
    raise ValueError("jobs file must be JSON array or {\"jobs\": [...]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stealth Local Research Agent v1.0.0",
    )
    parser.add_argument("--goal", default="", help="調査ゴール")
    parser.add_argument("--focus", default="", help="調査フォーカス")
    parser.add_argument("--jobs-file", default="", help="JSON / JSONL のバッチジョブファイル")
    parser.add_argument("--output-dir", default="", help="出力先ディレクトリ（単発実行時）")

    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--ollama-timeout", type=int, default=120)
    parser.add_argument("--fast-model", default=DEFAULT_FAST_MODEL)
    parser.add_argument("--accurate-model", default=DEFAULT_ACCURATE_MODEL)

    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--max-claims", type=int, default=20)
    parser.add_argument("--max-evidence-per-claim", type=int, default=6)
    parser.add_argument("--max-doc-chars", type=int, default=7000)

    parser.add_argument("--max-urls", type=int, default=30)
    parser.add_argument("--max-fetches", type=int, default=80)
    parser.add_argument("--max-time", type=float, default=480.0)
    parser.add_argument("--max-results-per-provider", type=int, default=8)

    parser.add_argument("--json", action="store_true", help="結果をJSONで標準出力")
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    root = repo_root()
    ensure_runtime_import_paths(root)

    jobs: List[Dict[str, Any]]
    if args.jobs_file:
        jobs = load_jobs_file(Path(args.jobs_file).resolve())
    else:
        if not compact_text(args.goal):
            raise SystemExit("--goal or --jobs-file is required")
        jobs = [{"goal": args.goal, "focus": args.focus, "output_dir": args.output_dir}]

    results: List[JobResult] = []
    for idx, job in enumerate(jobs, 1):
        try:
            result = execute_single_job(job, args)
            results.append(result)
            print(
                f"[{idx}/{len(jobs)}] success: goal='{result.goal}' "
                f"output='{result.output_dir}'"
            )
        except Exception as exc:
            failed_goal = compact_text(str(job.get("goal", "")))
            output_dir = ensure_job_output_dir(failed_goal or "failed_job", str(job.get("output_dir", "")))
            error_payload = {
                "goal": failed_goal,
                "error": f"{type(exc).__name__}: {exc}",
                "generated_at": now_iso(),
            }
            write_json(output_dir / "error.json", error_payload)
            results.append(JobResult(
                goal=failed_goal,
                focus=compact_text(str(job.get("focus", ""))),
                status="failed",
                output_dir=str(output_dir),
                report_path="",
                error=error_payload["error"],
            ))
            print(f"[{idx}/{len(jobs)}] failed: goal='{failed_goal}' error='{error_payload['error']}'")

    batch_summary = {
        "generated_at": now_iso(),
        "job_count": len(results),
        "success_count": sum(1 for r in results if r.status == "success"),
        "failed_count": sum(1 for r in results if r.status != "success"),
        "results": [r.__dict__ for r in results],
    }

    if args.jobs_file:
        batch_out = root / DEFAULT_OUTPUT_ROOT / f"batch_{now_stamp()}"
        batch_out.mkdir(parents=True, exist_ok=True)
        write_json(batch_out / "batch_summary.json", batch_summary)
        batch_summary["batch_summary_path"] = str(batch_out / "batch_summary.json")

    if args.json:
        print(json.dumps(batch_summary, ensure_ascii=False, indent=2))

    return 0 if batch_summary["failed_count"] == 0 else 2


if __name__ == "__main__":
    shared_path = repo_root() / ".agent" / "workflows" / "shared"
    if str(shared_path) not in sys.path:
        sys.path.insert(0, str(shared_path))
    try:
        from workflow_logging_hook import run_logged_main  # type: ignore
    except Exception:
        raise SystemExit(run())
    raise SystemExit(run_logged_main(
        "research_stealth_local",
        "research_stealth_local",
        run,
        phase_name="RESEARCH_STEALTH_LOCAL",
    ))
