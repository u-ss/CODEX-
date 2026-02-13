# -*- coding: utf-8 -*-
"""
オーケストレーター — Search → Fetch → Extract 統合ランナー
CODEXAPP設計: 全レイヤでTOOL_CALL/TOOL_RESULT、予算管理、ブレーカー統合
v2.0: RateLimiter統合、抽出品質メトリクス、URL選定透明性
"""
from __future__ import annotations

import hashlib
from collections import deque
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .config import StealthResearchConfig
from .extract import compute_metrics, extract_text_from_html
from .fetch.circuit_breaker import CircuitBreaker
from .fetch.cache import UrlCache
from .fetch.compliance import ComplianceChecker
from .fetch.host_policy import HostPolicyEngine
from .fetch.http_fetcher import FetchResult, HttpFetcher
from .fetch.link_tracker import LinkTracker
from .fetch.rate_limiter import RateLimiter
from .fetch.retry_policy import RetryPolicy
from .logging.events import EventLogger, RunSummary, ToolResultEvent
from .search.bing_rss import BingRssSearch, SearchResult
from .search.federation import FederatedSearch
from .verify.checks import Verifier


@dataclass
class ResearchResult:
    """リサーチ結果"""
    run_id: str = ""
    queries: list = None
    urls_found: list = None
    fetched_contents: list = None
    summary: dict = None
    verification: dict = None
    output_dir: str = ""

    def __post_init__(self):
        self.queries = self.queries or []
        self.urls_found = self.urls_found or []
        self.fetched_contents = self.fetched_contents or []
        self.summary = self.summary or {}
        self.verification = self.verification or {}


class Orchestrator:
    """Search → Fetch → Extract 統合オーケストレーター"""

    def __init__(self, config: Optional[StealthResearchConfig] = None):
        self.config = config or StealthResearchConfig()
        self.retry_policy = RetryPolicy(self.config.retry)
        self.breaker = CircuitBreaker(self.config.breaker)
        self.host_policy = HostPolicyEngine()
        self.url_cache = UrlCache()  # v3.0: URLキャッシュ
        self.compliance = ComplianceChecker()  # P2-B: robots.txt
        self.rate_limiter = RateLimiter(self.config.rate_limit)
        self.fetcher = HttpFetcher(self.config.fetch)
        self.link_tracker = LinkTracker()  # P2-A: リンク追跡
        # v3.1: config.search.providersを反映（CODEX指摘修正）
        self.searcher = self._build_searcher()
        self.verifier = Verifier()

    def _build_searcher(self) -> FederatedSearch:
        """設定に基づいてFederatedSearchを構築"""
        from .search.federation import BingRssSearch, DuckDuckGoSearch, SearchProvider
        provider_map = {
            "bing_rss": BingRssSearch,
            "duckduckgo": DuckDuckGoSearch,
        }
        providers = []
        for name in self.config.search.providers:
            cls = provider_map.get(name)
            if cls:
                providers.append(cls())
        if not providers:
            # フォールバック: デフォルト
            return FederatedSearch()
        return FederatedSearch(providers=providers)

    def run(
        self,
        queries: List[str],
        output_dir: Optional[str] = None,
    ) -> ResearchResult:
        """
        メインリサーチ実行。

        Args:
            queries: 検索クエリのリスト
            output_dir: 出力ディレクトリ

        Returns:
            ResearchResult
        """
        out_dir = output_dir or self.config.log.artifact_dir
        os.makedirs(out_dir, exist_ok=True)
        logger = EventLogger(out_dir)
        run_id = logger.run_id

        start_time = time.time()
        all_urls: List[str] = []
        fetched: List[Dict] = []
        total_fetches = 0
        successful = 0
        failed = 0
        skipped = 0
        breaker_opens = 0
        extraction_ratios: List[float] = []
        truncated_count = 0
        quality_grades: List[str] = []

        # === SEARCH フェーズ ===
        for query in queries:
            # 予算チェック
            if time.time() - start_time > self.config.budget.max_time_sec:
                break

            call_event = logger.log_tool_call(
                tool_name="search_web",
                args={"query": query, "provider": "federated"},
            )

            search_start = time.time()
            results = self.searcher.search(
                query, self.config.search.max_results_per_provider
            )
            search_ms = (time.time() - search_start) * 1000

            urls = [r.url for r in results]
            all_urls.extend(urls)

            logger.log_tool_result(
                tool_name="search_web",
                span_id=call_event.span_id,
                query=query,
                provider="federated",
                results_count=len(results),
                status="success" if results else "failed",
                duration_ms=search_ms,
            )

        # === URL選定フェーズ（P0-D: 選定透明性） ===
        total_before_dedup = len(all_urls)
        seen = set()
        unique_urls = []
        for u in all_urls:
            # URL正規化ベースの重複排除（P1-B）
            if self.url_cache.is_duplicate(u, seen):
                logger.log_tool_result(
                    tool_name="url_selection",
                    url=u,
                    status="skipped",
                    selection_reason="duplicate",
                    skip_reason="duplicate_canonical",
                )
            else:
                unique_urls.append(u)

        # v3.1: 予算前プリフィルタ（host_policy + robots: CODEX指摘修正）
        prefiltered = []
        for u in unique_urls:
            pd = self.host_policy.check(u)
            if not pd.allowed:
                logger.log_tool_result(
                    tool_name="url_selection", url=u,
                    status="skipped", selection_reason="policy_prefilter",
                    skip_reason=f"host_policy:{pd.state.value}",
                )
                continue
            if not self.compliance.is_allowed(u):
                logger.log_tool_result(
                    tool_name="url_selection", url=u,
                    status="skipped", selection_reason="robots_prefilter",
                    skip_reason="robots_txt_disallowed",
                )
                continue
            prefiltered.append(u)

        # URL予算チェック — プリフィルタ後の予算適用
        budget_urls = prefiltered[:self.config.budget.max_urls]
        for u in prefiltered[self.config.budget.max_urls:]:
            logger.log_tool_result(
                tool_name="url_selection",
                url=u,
                status="skipped",
                selection_reason="budget_exceeded",
                skip_reason="budget_exceeded",
            )
        # 選定されたURLをログ記録
        for u in budget_urls:
            logger.log_tool_result(
                tool_name="url_selection",
                url=u,
                status="success",
                selection_reason="selected",
            )

        unique_urls = budget_urls
        # クエリキーワード抽出（リンク追跡用）
        query_keywords = []
        for q in queries:
            query_keywords.extend(q.lower().split())

        # === FETCH フェーズ（dequeベース: リンク追跡で動的追加可能） ===
        url_queue = deque(unique_urls)
        fetched_urls_set = set(unique_urls)  # 重複防止
        tracked_count = 0  # リンク追跡で追加した数

        while url_queue:
            url = url_queue.popleft()

            # 時間予算チェック
            if time.time() - start_time > self.config.budget.max_time_sec:
                break
            # フェッチ回数予算チェック
            if total_fetches >= self.config.budget.max_fetches:
                break

            host = urlparse(url).netloc.lower() if url else "unknown"

            # === Host Policy チェック（P0-A: ブレーカーより前） ===
            policy_decision = self.host_policy.check(url)
            if not policy_decision.allowed:
                skipped += 1
                logger.log_tool_result(
                    tool_name="fetch_url",
                    url=url,
                    host=host,
                    status="skipped",
                    skip_reason=f"host_policy:{policy_decision.state.value}",
                    retry_decision="policy_blocked",
                    decision_reason=policy_decision.reason,
                    quality_grade="unavailable",
                )
                quality_grades.append("unavailable")
                continue

            # === robots.txtコンプライアンスチェック（P2-B） ===
            if not self.compliance.is_allowed(url):
                skipped += 1
                logger.log_tool_result(
                    tool_name="fetch_url",
                    url=url,
                    host=host,
                    status="skipped",
                    skip_reason="robots_txt_disallowed",
                    retry_decision="compliance_blocked",
                    decision_reason="robots.txt disallow",
                    quality_grade="unavailable",
                )
                quality_grades.append("unavailable")
                continue

            # ブレーカーチェック
            breaker_decision = self.breaker.check(url)
            if not breaker_decision.allowed:
                skipped += 1
                if breaker_decision.reason == "open":
                    breaker_opens += 1
                logger.log_tool_result(
                    tool_name="fetch_url",
                    url=url,
                    host=breaker_decision.host,
                    status="skipped",
                    skip_reason=breaker_decision.reason,
                    retry_decision="breaker_open",
                    decision_reason=breaker_decision.reason,
                    quality_grade="skipped",
                )
                quality_grades.append("skipped")
                continue

            # === Crawl-Delay適用（P2-B: CODEX指摘修正） ===
            crawl_delay = self.compliance.get_crawl_delay(url)
            if crawl_delay > 0:
                self.rate_limiter.set_retry_after(host, crawl_delay)

            # === RateLimiter適用（P0-B） ===
            rl_result = self.rate_limiter.acquire(host)

            # フェッチ実行（リトライループ）
            for attempt in range(1, self.config.retry.max_attempts_per_url + 1):
                total_fetches += 1

                call_event = logger.log_tool_call(
                    tool_name="fetch_url",
                    args={
                        "url": url,
                        "attempt_no": attempt,
                        "engine": "http",
                        "rate_limit_wait_sec": round(rl_result.waited_sec, 3),
                    },
                )

                # URLキャッシュ conditional GET（CODEX指摘修正）
                cond_headers = self.url_cache.get_conditional_headers(url)
                result = self.fetcher.fetch(url, extra_headers=cond_headers)

                # 304 Not Modified処理
                if result.status_code == 304:
                    cached = self.url_cache.get(url)
                    if cached:
                        result = FetchResult(
                            url=url,
                            content=cached.content,
                            status_code=200,
                            final_url=url,
                            headers=result.headers,
                            duration_ms=result.duration_ms,
                        )
                # コンテンツ処理
                content_sha = ""
                content_preview = ""
                artifact_id = ""
                ext_ratio = 0.0
                bp_ratio = 0.0
                was_truncated = False
                q_grade = ""

                if result.content:
                    content_sha = EventLogger.content_sha256(result.content)
                    content_preview = result.content[:self.config.log.log_content_preview_chars]
                    # アーティファクトとして保存（生HTML + 抽出テキスト）
                    artifact_id = f"{run_id}_{hashlib.md5(url.encode()).hexdigest()[:8]}"
                    artifact_path = Path(out_dir) / f"{artifact_id}.txt"
                    with open(artifact_path, "w", encoding="utf-8") as f:
                        f.write(result.content)

                    # 抽出品質メトリクス（P0-C）
                    metrics = compute_metrics(
                        raw_content=result.content,
                        extracted_content=result.content,
                        max_chars=self.config.fetch.max_chars,
                    )
                    ext_ratio = metrics.extraction_ratio
                    bp_ratio = metrics.boilerplate_ratio
                    was_truncated = metrics.was_truncated
                    extraction_ratios.append(ext_ratio)
                    if was_truncated:
                        truncated_count += 1
                    q_grade = metrics.quality_grade
                    quality_grades.append(q_grade)

                    # 抽出テキストも別ファイルに保存（品質改善）
                    extracted_text = extract_text_from_html(result.content)
                    if extracted_text:
                        ext_path = Path(out_dir) / f"{artifact_id}_extracted.txt"
                        with open(ext_path, "w", encoding="utf-8") as f:
                            f.write(extracted_text)

                # リトライ判定
                retry_dec = self.retry_policy.decide(
                    status_code=result.status_code,
                    attempt_no=attempt,
                    error_class=result.error_class,
                    retry_after_header=result.headers.get("Retry-After"),
                )

                # Retry-AfterヘッダーをRateLimiterに記録
                if result.headers.get("Retry-After"):
                    try:
                        ra_sec = float(result.headers["Retry-After"])
                        self.rate_limiter.set_retry_after(host, ra_sec)
                    except (ValueError, TypeError):
                        pass

                # ブレーカーに記録
                if result.success:
                    self.breaker.record_success(url)
                elif result.blocked_signal or result.error_class:
                    self.breaker.record_failure(url, result.blocked_signal)

                # Host Policyに結果を報告（P0-A）
                self.host_policy.report_result(url, result.status_code or 0)
                # JS必須判定
                if result.content:
                    self.host_policy.detect_js_required(url, result.content)

                logger.log_tool_result(
                    tool_name="fetch_url",
                    span_id=call_event.span_id,
                    url=url,
                    final_url=result.final_url,
                    host=result.host,
                    engine="http",
                    http_status=result.status_code,
                    error_class=result.error_class,
                    blocked_signal=result.blocked_signal,
                    attempt_no=attempt,
                    retry_decision="retry" if retry_dec.should_retry else "no_retry",
                    decision_reason=retry_dec.reason,
                    content_preview=content_preview,
                    content_sha256=content_sha,
                    content_artifact_id=artifact_id,
                    content_length=len(result.content),
                    status="success" if result.success else "failed",
                    duration_ms=result.duration_ms,
                    # P0-B: RateLimiter証跡
                    rate_limit_wait_sec=round(rl_result.waited_sec, 3),
                    retry_after_respected=rl_result.retry_after_respected,
                    # P0-C: 抽出品質メトリクス
                    extraction_ratio=ext_ratio,
                    boilerplate_ratio=bp_ratio,
                    was_truncated=was_truncated,
                    quality_grade=q_grade if result.content else "",
                )

                if result.success:
                    successful += 1
                    # URLキャッシュにフェッチ結果を保存（CODEX指摘修正）
                    self.url_cache.put(
                        url=url,
                        content=result.content,
                        content_hash=content_sha,
                        etag=result.headers.get('ETag', ''),
                        last_modified=result.headers.get('Last-Modified', ''),
                        content_length=len(result.content),
                        status_code=result.status_code or 200,
                    )
                    fetched.append({
                        "url": url,
                        "content": result.content,
                        "content_sha256": content_sha,
                        "artifact_id": artifact_id,
                    })
                    # === P2-A: リンク追跡（成功フェッチから追加URL抽出） ===
                    if (result.content and tracked_count < 10
                            and total_fetches < self.config.budget.max_fetches):
                        tracked_links = self.link_tracker.extract_links(
                            html=result.content,
                            source_url=url,
                            query_keywords=query_keywords,
                            already_seen=fetched_urls_set,
                        )
                        for tl in tracked_links:
                            if tl.url not in fetched_urls_set:
                                url_queue.append(tl.url)
                                fetched_urls_set.add(tl.url)
                                tracked_count += 1
                                logger.log_tool_result(
                                    tool_name="link_tracking",
                                    url=tl.url,
                                    source_url=tl.source_url,
                                    relevance_score=round(tl.relevance_score, 3),
                                    anchor_text=tl.anchor_text[:100],
                                    status="queued",
                                )
                    break
                elif not retry_dec.should_retry:
                    failed += 1
                    break
                else:
                    # リトライ待機（同期版）
                    time.sleep(retry_dec.wait_sec)
                    # === CODEX指摘修正: retry時もcrawl-delay適用 ===
                    rl_result = self.rate_limiter.acquire(host)

            # RateLimiter解放
            self.rate_limiter.release(host)

        # === RateLimiter統計取得（P0-B） ===
        rl_stats = self.rate_limiter.get_stats()

        # === VERIFY フェーズ ===
        avg_ext_ratio = (
            sum(extraction_ratios) / len(extraction_ratios)
            if extraction_ratios else 0.0
        )

        # 品質グレード集計
        quality_counts = {}
        for g in quality_grades:
            quality_counts[g] = quality_counts.get(g, 0) + 1

        run_summary = RunSummary(
            run_id=run_id,
            start_time=start_time,
            end_time=time.time(),
            total_queries=len(queries),
            total_urls_found=len(unique_urls),
            total_urls_before_dedup=total_before_dedup,
            total_fetches=total_fetches,
            successful_fetches=successful,
            failed_fetches=failed,
            skipped_fetches=skipped,
            breaker_opens=breaker_opens,
            total_rate_limit_waits=rl_stats.get("total_waits", 0),
            total_rate_limit_wait_sec=rl_stats.get("total_wait_sec", 0.0),
            avg_extraction_ratio=round(avg_ext_ratio, 4),
            truncated_count=truncated_count,
            quality_counts=quality_counts,
            claimed_success=successful > 0,
        )

        verification = self.verifier.verify(
            logger.get_all_events(), run_summary,
            budget_max_fetches=self.config.budget.max_fetches,
        )
        run_summary.verified_success = verification.verified_success
        run_summary.verification_checks = {
            c.name: c.passed for c in verification.checks
        }

        # 保存
        logger.save_summary(run_summary)

        return ResearchResult(
            run_id=run_id,
            queries=queries,
            urls_found=unique_urls,
            fetched_contents=fetched,
            summary=asdict(run_summary),
            verification=verification.to_dict(),
            output_dir=out_dir,
        )

    def close(self):
        """リソース解放"""
        self.fetcher.close()
