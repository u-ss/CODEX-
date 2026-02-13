# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Phase 1 Handler (WIDE)
広域調査: 7視点分解 → 検索 → RawClaim抽出

LLM責務:
- クエリを7視点に分解
- 検索結果からRawClaim抽出

純粋ロジック:
- 重複URL除去
- ログ整形
- RawClaimRecord変換
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from hashlib import sha256

from ..context import ResearchRunContext
from ..phase_runner import Phase, PhaseResult, PhaseSignal
from ..tool_trace import call_tool


# 7視点テンプレート
SEVEN_PERSPECTIVES = [
    "定義・概念（{query}とは何か）",
    "現状・実態（{query}の現在の状況）",
    "原因・背景（{query}がなぜ起きているか）",
    "影響・効果（{query}によって何が変わるか）",
    "対策・解決策（{query}にどう対処するか）",
    "比較・代替案（{query}と他の選択肢の比較）",
    "将来予測（{query}は今後どうなるか）",
]


def make_wide_handler(
    tools: Optional[Any] = None,
    llm: Optional[Any] = None,
    config: Optional[Dict] = None
) -> Callable[[ResearchRunContext], PhaseResult]:
    """
    Phase 1 (WIDE) ハンドラを生成
    
    Args:
        tools: ツール呼び出しインターフェース
               - tools.search_web(query) -> List[Dict]
               - tools.read_url_content(url) -> str
        llm: LLM呼び出しインターフェース
             - llm.extract_claims(text, query) -> List[Dict]
        config: 設定
    """
    cfg = config or {}
    max_search_results = cfg.get("max_search_results", 10)
    max_urls_to_read = cfg.get("max_urls_to_read", 12)
    max_search_queries = cfg.get("max_search_queries", 21)  # SKILL目安: 15-21
    
    def handler(context: ResearchRunContext) -> PhaseResult:
        """Phase 1: 広域調査"""
        query = context.query
        raw_claims: List[Dict] = []
        search_log: List[Dict] = []
        seen_urls: set = set()
        
        # ツールがない場合はスタブモード
        if tools is None:
            return _stub_result(query)
        
        try:
            # 1. 7視点に基づく検索クエリ生成
            if llm and hasattr(llm, "generate_queries"):
                search_queries = call_tool(
                    context,
                    tool_name="llm.generate_queries",
                    call=lambda: llm.generate_queries(query, SEVEN_PERSPECTIVES),
                    args={"query": query, "perspectives_count": len(SEVEN_PERSPECTIVES)},
                    result_summary=lambda items: {"queries_count": len(items or [])},
                )
            else:
                search_queries = _generate_search_queries(query, llm=None)
            search_queries = [q for q in search_queries if q][:max_search_queries]
            
            # 2. 各クエリで検索
            all_results: List[Dict] = []
            for sq in search_queries:
                try:
                    results = call_tool(
                        context,
                        tool_name="tools.search_web",
                        call=lambda sq=sq: tools.search_web(sq),
                        args={"query": sq},
                        result_summary=lambda rows: {"results_count": len(rows or [])},
                    )
                    search_log.append({
                        "query": sq,
                        "result_count": len(results) if results else 0,
                        "timestamp": datetime.now().isoformat()
                    })
                    if results:
                        all_results.extend(results[:max_search_results])
                except Exception as e:
                    search_log.append({
                        "query": sq,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    })
            
            # 3. URL重複除去
            unique_urls = []
            for r in all_results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_urls.append(r)
            
            # 4. コンテンツ読み取り & Claim抽出
            for item in unique_urls[:max_urls_to_read]:
                url = item.get("url", "")
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
                        call_tool(
                            context,
                            tool_name="tools.read_url_content",
                            call=lambda: None,
                            args={"url": url, "skipped": True, "reason": reason},
                            result_summary=lambda _: {"status": "skipped", "reason": reason},
                        )
                        search_log.append({
                            "url": url,
                            "skipped": True,
                            "reason": reason,
                            "timestamp": datetime.now().isoformat()
                        })
                        continue

                try:
                    content = call_tool(
                        context,
                        tool_name="tools.read_url_content",
                        call=lambda url=url: tools.read_url_content(url),
                        args={"url": url},
                        result_summary=lambda text: {"chars": len(text or "")},
                    )
                    if content:
                        context.seen_urls.add(url)
                        if breaker:
                            breaker.record_success(url)
                    if content and llm:
                        # LLMでClaim抽出
                        claims = call_tool(
                            context,
                            tool_name="llm.extract_claims",
                            call=lambda: llm.extract_claims(content, query),
                            args={"query": query, "content_chars": len(content)},
                            result_summary=lambda rows: {"claims_count": len(rows or [])},
                        )
                        for claim in claims:
                            raw_claims.append({
                                "claim_text": claim.get("text", ""),
                                "source_url": url,
                                "published_at": item.get("published_at"),
                                "extracted_at": datetime.now().isoformat(),
                                "fingerprint": _fingerprint(claim.get("text", ""))
                            })
                    elif content:
                        # LLMなし: コンテンツ先頭を仮Claimとして
                        raw_claims.append({
                            "claim_text": content[:500] + "..." if len(content) > 500 else content,
                            "source_url": url,
                            "extracted_at": datetime.now().isoformat(),
                            "fingerprint": _fingerprint(content[:500])
                        })
                except Exception as e:
                    # 失敗をサーキットブレーカーに記録
                    error_text = str(e)
                    error_code = None
                    if "403" in error_text:
                        error_code = 403
                    elif "401" in error_text:
                        error_code = 401
                    elif "404" in error_text:
                        error_code = 404
                    if breaker:
                        breaker.record_failure(url, error_code, error_text)
                    search_log.append({
                        "url": url,
                        "error": error_text,
                        "timestamp": datetime.now().isoformat()
                    })
            
            # 5. コンテキストに出力を格納
            context.raw_claims = raw_claims
            context.search_log = search_log
            
            return PhaseResult(
                phase=Phase.WIDE,
                success=True,
                signal=PhaseSignal.NEXT,
                output={
                    "raw_claims_count": len(raw_claims),
                    "urls_processed": len(unique_urls[:max_urls_to_read]),
                    "search_queries": search_queries
                },
                notes=f"Phase 1完了: {len(raw_claims)}件のRawClaim抽出"
            )
            
        except Exception as e:
            return PhaseResult(
                phase=Phase.WIDE,
                success=False,
                signal=PhaseSignal.ABORT,
                error=str(e)
            )
    
    return handler


def _generate_search_queries(query: str, llm: Optional[Any] = None) -> List[str]:
    """7視点に基づく検索クエリを生成"""
    # テンプレートベース
    return [p.format(query=query) for p in SEVEN_PERSPECTIVES]


def _fingerprint(text: str) -> str:
    """テキストのフィンガープリントを生成"""
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def _stub_result(query: str) -> PhaseResult:
    """ツールなし時のスタブ結果"""
    return PhaseResult(
        phase=Phase.WIDE,
        success=True,
        signal=PhaseSignal.NEXT,
        output={"mode": "stub", "query": query},
        notes="Phase 1 (stub): ツール未設定のためスキップ"
    )
