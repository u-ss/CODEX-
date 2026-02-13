# -*- coding: utf-8 -*-
"""
VERIFICATION_RUN チェック — 監査で「本当に成功したか」を検証
CODEXAPP設計: verified_successはここでしかtrueにならない
v2.0: 異常系実証チェック、RateLimiter証跡チェック、抽出品質チェック追加
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..logging.events import RunSummary, ToolResultEvent


@dataclass
class CheckResult:
    """個別チェックの結果"""
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult:
    """VERIFICATION_RUN全体の結果"""
    verified_success: bool
    checks: List[CheckResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict:
        return {
            "verified_success": self.verified_success,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "summary": self.summary,
        }


class Verifier:
    """VERIFICATION_RUN実装"""

    def verify(
        self,
        events: List[Dict],
        run_summary: RunSummary,
        budget_max_fetches: int = 100,
    ) -> VerificationResult:
        """
        全チェックを実行し、verified_successを判定。

        Args:
            events: L3 TraceイベントのリストDicts
            run_summary: L1 Summary
        """
        checks = [
            self._check_no_403_infinite_loop(events),
            self._check_max_host_403_count(events),
            self._check_search_queries_exist(events),
            self._check_breaker_honored(events),
            self._check_budget_not_exceeded(events, budget_max_fetches),
            self._check_has_successful_content(events),
            # v2.0追加チェック
            self._check_retry_decisions_logged(events),
            self._check_rate_limiter_evidence(events, run_summary),
            self._check_extraction_quality(events, run_summary),
            self._check_url_selection_logged(events),
        ]

        all_passed = all(c.passed for c in checks)

        return VerificationResult(
            verified_success=all_passed,
            checks=checks,
            summary=f"{sum(1 for c in checks if c.passed)}/{len(checks)} checks passed",
        )

    def _check_no_403_infinite_loop(self, events: List[Dict]) -> CheckResult:
        """同一URLへの403試行が2回以下"""
        url_403_counts: Dict[str, int] = {}
        for e in events:
            if e.get("event_type") == "TOOL_RESULT" and e.get("http_status") == 403:
                url = e.get("url", "")
                url_403_counts[url] = url_403_counts.get(url, 0) + 1

        max_streak = max(url_403_counts.values()) if url_403_counts else 0
        passed = max_streak <= 2
        worst_url = ""
        if url_403_counts:
            worst_url = max(url_403_counts, key=url_403_counts.get)

        return CheckResult(
            name="max_same_url_403_streak_le_2",
            passed=passed,
            detail=f"最大: {max_streak}回 (URL: {worst_url})" if not passed else f"最大: {max_streak}回",
        )

    def _check_max_host_403_count(self, events: List[Dict]) -> CheckResult:
        """Host単位の403回数が5回以下"""
        host_403: Dict[str, int] = {}
        for e in events:
            if e.get("event_type") == "TOOL_RESULT" and e.get("http_status") == 403:
                host = e.get("host", "unknown")
                host_403[host] = host_403.get(host, 0) + 1

        max_count = max(host_403.values()) if host_403 else 0
        passed = max_count <= 5
        worst = max(host_403, key=host_403.get) if host_403 else ""

        return CheckResult(
            name="max_host_403_count_le_5",
            passed=passed,
            detail=f"最大: {max_count}回 (Host: {worst})" if not passed else f"最大: {max_count}回",
        )

    def _check_search_queries_exist(self, events: List[Dict]) -> CheckResult:
        """少なくとも1つの検索クエリが実行されている"""
        queries = [
            e for e in events
            if e.get("event_type") == "TOOL_CALL" and e.get("tool_name") == "search_web"
        ]
        return CheckResult(
            name="search_queries_gt_0",
            passed=len(queries) > 0,
            detail=f"クエリ数: {len(queries)}",
        )

    def _check_breaker_honored(self, events: List[Dict]) -> CheckResult:
        """ブレーカーopenの後にskipされている"""
        open_events = [
            e for e in events
            if e.get("status") == "skipped" and e.get("skip_reason") in (
                "breaker_open", "open", "url_exhausted"
            )
        ]
        # ブレーカーが発動していない場合もOK
        has_breaker_failure = any(
            e.get("retry_decision") == "breaker_open" for e in events
        )
        if not has_breaker_failure:
            return CheckResult(
                name="breaker_honored",
                passed=True,
                detail="ブレーカー発動なし",
            )

        return CheckResult(
            name="breaker_honored",
            passed=len(open_events) > 0,
            detail=f"Skip数: {len(open_events)}",
        )

    def _check_budget_not_exceeded(
        self, events: List[Dict], max_fetches: int = 100,
    ) -> CheckResult:
        """取得予算を超過していない（設定連動）"""
        fetch_events = [
            e for e in events
            if e.get("event_type") == "TOOL_RESULT" and e.get("tool_name") == "fetch_url"
        ]
        return CheckResult(
            name="budget_not_exceeded",
            passed=len(fetch_events) <= max_fetches,
            detail=f"フェッチ数: {len(fetch_events)}/{max_fetches}",
        )

    def _check_has_successful_content(self, events: List[Dict]) -> CheckResult:
        """少なくとも1つの成功コンテンツがある"""
        success = [
            e for e in events
            if e.get("event_type") == "TOOL_RESULT"
            and e.get("status") == "success"
            and e.get("content_length", 0) > 0
        ]
        return CheckResult(
            name="has_successful_content",
            passed=len(success) > 0,
            detail=f"成功コンテンツ数: {len(success)}",
        )

    # === v2.0 追加チェック ===

    def _check_retry_decisions_logged(self, events: List[Dict]) -> CheckResult:
        """全fetchイベントにretry_decisionが記録されている"""
        fetch_results = [
            e for e in events
            if e.get("event_type") == "TOOL_RESULT" and e.get("tool_name") == "fetch_url"
        ]
        if not fetch_results:
            return CheckResult(
                name="retry_decisions_logged",
                passed=True,
                detail="フェッチイベントなし",
            )

        missing = [
            e for e in fetch_results
            if not e.get("retry_decision")
        ]
        return CheckResult(
            name="retry_decisions_logged",
            passed=len(missing) == 0,
            detail=f"記録済み: {len(fetch_results) - len(missing)}/{len(fetch_results)}",
        )

    def _check_rate_limiter_evidence(
        self, events: List[Dict], summary: RunSummary
    ) -> CheckResult:
        """RateLimiter証跡がログに存在する（待機秒数フィールドが全fetchに存在）"""
        fetch_results = [
            e for e in events
            if e.get("event_type") == "TOOL_RESULT" and e.get("tool_name") == "fetch_url"
        ]
        if not fetch_results:
            return CheckResult(
                name="rate_limiter_evidence",
                passed=True,
                detail="フェッチイベントなし",
            )

        # rate_limit_wait_secフィールドの存在チェック
        has_field = [
            e for e in fetch_results
            if "rate_limit_wait_sec" in e
        ]
        total_wait = summary.total_rate_limit_wait_sec

        return CheckResult(
            name="rate_limiter_evidence",
            passed=len(has_field) == len(fetch_results),
            detail=f"証跡あり: {len(has_field)}/{len(fetch_results)}, 合計待機: {total_wait:.2f}s",
        )

    def _check_extraction_quality(
        self, events: List[Dict], summary: RunSummary
    ) -> CheckResult:
        """抽出品質: 存在＋閾値チェック（v3.1: 閾値型に強化）"""
        success_fetches = [
            e for e in events
            if e.get("event_type") == "TOOL_RESULT"
            and e.get("tool_name") == "fetch_url"
            and e.get("status") == "success"
        ]
        if not success_fetches:
            return CheckResult(
                name="extraction_quality_logged",
                passed=True,
                detail="成功フェッチなし",
            )

        has_metrics = [
            e for e in success_fetches
            if "extraction_ratio" in e
        ]
        truncated = summary.truncated_count
        avg_ratio = summary.avg_extraction_ratio

        # v3.1: 閾値チェック追加
        metrics_present = len(has_metrics) == len(success_fetches)
        min_ratio_ok = avg_ratio >= 0.03  # 最低抽出率3%
        quality_counts = getattr(summary, 'quality_counts', {}) or {}
        total_q = sum(quality_counts.values()) if quality_counts else 0
        empty_count = quality_counts.get("empty", 0)
        empty_ratio = empty_count / total_q if total_q > 0 else 0.0
        empty_ok = empty_ratio <= 0.5  # empty比率50%以下

        passed = metrics_present and min_ratio_ok and empty_ok
        reasons = []
        if not metrics_present:
            reasons.append("メトリクス欠落")
        if not min_ratio_ok:
            reasons.append(f"抽出率低({avg_ratio:.2%}<3%)")
        if not empty_ok:
            reasons.append(f"empty過多({empty_ratio:.0%}>50%)")

        return CheckResult(
            name="extraction_quality_logged",
            passed=passed,
            detail=f"メトリクスあり: {len(has_metrics)}/{len(success_fetches)}, "
                   f"平均抽出率: {avg_ratio:.2%}, 切り詰め: {truncated}件"
                   + (f", FAIL: {', '.join(reasons)}" if reasons else ""),
        )

    def _check_url_selection_logged(self, events: List[Dict]) -> CheckResult:
        """URL選定イベントが記録されている"""
        selection_events = [
            e for e in events
            if e.get("tool_name") == "url_selection"
        ]
        selected = [e for e in selection_events if e.get("selection_reason") == "selected"]
        duplicates = [e for e in selection_events if e.get("selection_reason") == "duplicate"]
        budget_exceeded = [e for e in selection_events if e.get("selection_reason") == "budget_exceeded"]

        return CheckResult(
            name="url_selection_logged",
            passed=len(selection_events) > 0,
            detail=f"選定: {len(selected)}, 重複除外: {len(duplicates)}, 予算超過: {len(budget_exceeded)}",
        )
