# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Orchestrator Module
統合オーケストレーション（実行エンジン本体）

GPT-5.2設計: ArtifactWriter + FailureDetector + Termination を束ねる
- Phase実行ループ
- 自動保存
- 失敗検知・ログ
- 終了条件判定
- 差し戻しループ制御
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from contextlib import nullcontext
from typing import Optional, Callable, Dict, Any
import sys
import os

from .context import ResearchRunContext
from .phase_runner import PhaseRunner, Phase, PhaseSignal, PhaseResult, stub_handler
from .artifacts import ArtifactWriter, RawClaimRecord, NormalizedClaimRecord, EvidenceRecord, VerifiedClaimRecord, CounterevidenceLog
from .failure_detector import FailureDetector, FailureEvent, check_verification_failures
from .termination import should_stop, RoundSnapshot, ClaimSnapshot
from .models import generate_evidence_id
from .capsules import build_capsule, append_capsule
from .tool_trace import call_tool
from .circuit_breaker import ResearchUrlCircuitBreaker

try:
    from knowledge.learning import AgentEvent, get_client, make_signature_key
except Exception:  # pragma: no cover
    AgentEvent = None  # type: ignore
    get_client = None  # type: ignore
    make_signature_key = None  # type: ignore


@dataclass
class OrchestratorConfig:
    """オーケストレーター設定"""
    output_dir: Optional[Path] = None
    max_verify_rollbacks: int = 2
    save_after_each_phase: bool = True
    log_failures: bool = True
    verbose: bool = True
    tools: Optional[Any] = None
    llm: Optional[Any] = None
    handler_config: Optional[Dict[str, Any]] = None


class ResearchOrchestrator:
    """
    リサーチエージェント実行エンジン
    
    使用例:
        orchestrator = ResearchOrchestrator(query="AIエージェントの最新動向")
        result = orchestrator.run()
        print(result.get_summary())
    """
    
    def __init__(
        self,
        query: str,
        config: Optional[OrchestratorConfig] = None,
        custom_handlers: Optional[Dict[Phase, Callable]] = None
    ):
        self.config = config or OrchestratorConfig()
        self._event_info, self._event_warn, self._event_error = self._try_setup_logger()
        
        # コンテキスト初期化
        self.context = ResearchRunContext(
            query=query,
            max_verify_rollbacks=self.config.max_verify_rollbacks
        )
        
        # ArtifactWriter
        self.writer = ArtifactWriter(
            session_id=self.context.session_id,
            output_dir=self.config.output_dir
        )
        self.context.output_dir = self.writer.get_output_path()
        
        # FailureDetector
        self.detector = FailureDetector(
            log_path=self.writer.get_output_path() / "failures.jsonl"
        )
        
        # PhaseRunner
        self.runner = PhaseRunner()
        self._register_handlers(custom_handlers)
        self._logged_main, self._phase_scope = self._try_setup_workflow_logging()
        
        # サーキットブレーカー（403再試行ループ抑止）
        self.context.circuit_breaker = ResearchUrlCircuitBreaker()

    def _try_setup_logger(self):
        try:
            repo_root = Path(__file__).resolve().parents[4]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            from lib.logger import setup_logger, info, warn, error  # type: ignore

            setup_logger(path=repo_root / "_logs" / "research.jsonl")
            return info, warn, error
        except Exception:
            def _noop(*args: Any, **kwargs: Any) -> None:
                return

            return _noop, _noop, _noop

    def _try_setup_workflow_logging(self):
        """WorkflowLogger連携（利用可能な場合のみ）。"""
        if os.getenv("RESEARCH_DISABLE_WORKFLOW_LOGGING", "").strip() in {"1", "true", "TRUE"}:
            return None, None
        try:
            shared_dir = Path(__file__).resolve().parents[2] / "shared"
            if str(shared_dir) not in sys.path:
                sys.path.insert(0, str(shared_dir))
            from workflow_logging_hook import logged_main, phase_scope  # type: ignore

            return logged_main, phase_scope
        except Exception:
            return None, None
    
    def _register_handlers(self, custom_handlers: Optional[Dict[Phase, Callable]]):
        """ハンドラを登録"""
        # v4.3.3: handlersパッケージからデフォルトハンドラを取得
        try:
            from .handlers import make_default_handlers
            default_handlers = make_default_handlers(
                tools=self.config.tools,
                llm=self.config.llm,
                config=self.config.handler_config or {},
            )
        except ImportError:
            default_handlers = {}
        
        for phase in [Phase.WIDE, Phase.NORMALIZE, Phase.DEEP, Phase.VERIFY, Phase.INTEGRATE]:
            if custom_handlers and phase in custom_handlers:
                self.runner.register(phase, custom_handlers[phase])
            elif phase in default_handlers:
                self.runner.register(phase, default_handlers[phase])
            else:
                # フォールバック: スタブハンドラ
                self.runner.register(phase, stub_handler(phase))
    
    def run(self) -> ResearchRunContext:
        """
        リサーチを実行
        
        Returns:
            完了後のコンテキスト
        """
        if self._logged_main and self._phase_scope:
            with self._logged_main("research", "research") as wf_logger:
                return self._run_impl(wf_logger=wf_logger)
        return self._run_impl(wf_logger=None)

    def _run_impl(self, wf_logger=None) -> ResearchRunContext:
        """実際の実行本体（WorkflowLogger連携有無を吸収）。"""
        self.context.workflow_logger = wf_logger
        self._log(f"🚀 リサーチ開始: {self.context.query}")
        self._log(f"   セッションID: {self.context.session_id}")
        self._log(f"   出力先: {self.context.output_dir}")
        if wf_logger is not None:
            wf_logger.set_input("query", self.context.query)
            wf_logger.set_input("session_id", self.context.session_id)
            wf_logger.set_input("output_dir", str(self.context.output_dir) if self.context.output_dir else "")
            wf_logger.set_input("max_verify_rollbacks", self.config.max_verify_rollbacks)
        self._event_info(
            "research_run_start",
            query=self.context.query,
            session_id=self.context.session_id,
            output_dir=str(self.context.output_dir) if self.context.output_dir else "",
        )
        
        while self.runner.current_phase != Phase.COMPLETE:
            # 現在のPhaseを実行
            phase_name = self.runner.get_phase_name()
            self._log(f"\n▶ {phase_name} 開始")
            phase_cm = (
                self._phase_scope(
                    wf_logger,
                    f"PHASE_{self.runner.current_phase.value.upper()}",
                    inputs={
                        "query": self.context.query,
                        "phase": self.runner.current_phase.value,
                        "phase_label": phase_name,
                        "history_count": len(self.runner.history),
                    },
                )
                if (wf_logger is not None and self._phase_scope is not None)
                else nullcontext()
            )

            with phase_cm as phase_logger:
                result = self.runner.run_current(self.context)
                if phase_logger is not None:
                    phase_logger.set_output("success", result.success)
                    phase_logger.set_output("signal", result.signal.value)
                    phase_logger.set_output("next_phase", self.runner.current_phase.value)
                    if result.error:
                        phase_logger.add_error(result.error, error_type="PhaseError")
                    if result.required_actions:
                        phase_logger.set_output("required_actions", result.required_actions)
            
            # 結果をログ
            self._log(f"  結果: {'✅ 成功' if result.success else '❌ 失敗'}")
            self._log(f"  シグナル: {result.signal.value}")
            self._event_info(
                "research_phase_result",
                phase=result.phase.value,
                success=bool(result.success),
                signal=result.signal.value,
            )
            
            # 失敗処理
            if not result.success and result.error:
                self._log(f"  エラー: {result.error}")
                self._event_error("research_phase_error", error_message=result.error, phase=result.phase.value)
                if self.config.log_failures:
                    event = self._create_failure_event(result)
                    self.context.add_failure(event)
                    self.detector.log_failure(event)
            
            # 保存
            if self.config.save_after_each_phase and result.success:
                self._save_phase_output(result.phase)
            
            # 差し戻し制御
            if result.signal == PhaseSignal.ROLLBACK:
                if self.context.can_rollback():
                    self.context.increment_rollback()
                    self.context.required_actions = result.required_actions
                    self._log(f"  ↩ 差し戻し: {result.rollback_to or Phase.DEEP}")
                else:
                    self._log(f"  ⚠ 差し戻し上限到達、続行")
                    result.signal = PhaseSignal.NEXT
            
            # 遷移
            prev_phase = self.runner.current_phase
            self.runner.transition(result)
            
            if prev_phase != self.runner.current_phase:
                self._log(f"  → {self.runner.get_phase_name()}")
            
            # Phase結果を記録
            self.context.phase_results.append({
                "phase": result.phase.value,
                "success": result.success,
                "signal": result.signal.value,
                "timestamp": datetime.now().isoformat()
            })
        
        self._log(f"\n🏁 リサーチ完了")
        self._validate_output_integrity()
        summary = self.context.get_summary()
        self._log(f"   サマリ: {summary}")
        if wf_logger is not None:
            wf_logger.set_output("summary", summary)
            evidence_required = int(summary.get("normalized_claims_count", 0)) > 0
            search_required = bool(str(summary.get("query", "")).strip())
            search_attempted = int(summary.get("search_queries_attempted", 0))
            checks = [
                {"name": "has_report", "pass": bool(summary.get("has_report"))},
                {"name": "failures_count_zero", "pass": int(summary.get("failures_count", 0)) == 0},
                {"name": "output_integrity_pass", "pass": bool(summary.get("output_integrity_pass"))},
            ]
            if search_required:
                checks.append({"name": "search_queries_attempted_gt_zero", "pass": search_attempted > 0})
            if evidence_required:
                checks.append({"name": "evidence_count_gt_zero", "pass": int(summary.get("evidence_count", 0)) > 0})
            # サーキットブレーカー検証: 同一URLの再試行が閾値以内か
            breaker_stats = summary.get("retry_guard_stats", {})
            max_url_attempts = breaker_stats.get("max_same_url_attempts", 0)
            checks.append({
                "name": "same_url_retry_guard_pass",
                "pass": max_url_attempts <= 2
            })
            verification_passed = all(bool(item["pass"]) for item in checks)
            verification_id = wf_logger.record_verification(
                checks=checks,
                passed=verification_passed,
                evidence={
                    "session_id": self.context.session_id,
                    "output_dir": str(self.context.output_dir),
                    "search_queries_attempted": search_attempted,
                    "missing_output_artifacts": summary.get("missing_output_artifacts", []),
                },
            )
            wf_logger.claim(
                "research_run_completed",
                evidence_refs=[verification_id],
                claimed_success=(
                    bool(summary.get("has_report"))
                    and bool(summary.get("output_integrity_pass"))
                    and verification_passed
                ),
            )
        self._event_info("research_run_finish", summary=summary, session_id=self.context.session_id)

        self._report_to_ki_learning()
        
        return self.context

    def _validate_output_integrity(self) -> None:
        """最終成果物の整合性を検証し、コンテキストへ反映する。"""
        output_dir = self.context.output_dir
        required = [
            "final_report.md",
            "audit_pack.json",
            "evidence.jsonl",
            "verified_claims.jsonl",
        ]
        if output_dir is None:
            self.context.output_integrity_pass = False
            self.context.missing_output_artifacts = required
            self._event_error("research_output_missing", missing=required, reason="output_dir_not_set")
            return

        missing = [name for name in required if not (output_dir / name).exists()]
        self.context.output_integrity_pass = len(missing) == 0
        self.context.missing_output_artifacts = missing
        if missing:
            self._event_error(
                "research_output_missing",
                output_dir=str(output_dir),
                missing=missing,
            )

    def _report_to_ki_learning(self) -> None:
        """
        KI Learning（knowledge/learning/learning.db）へ実行結果を記録。
        目的は「調査内容の知識化」ではなく、/research運用の失敗回避・品質改善のためのテレメトリ。
        """
        if AgentEvent is None or get_client is None or make_signature_key is None:
            return
        try:
            summary = self.context.get_summary()
            outcome = (
                "SUCCESS"
                if summary.get("has_report")
                and summary.get("output_integrity_pass")
                and summary.get("failures_count", 0) == 0
                else "PARTIAL"
            )
            confidence = 0.9 if outcome == "SUCCESS" else 0.55
            signature_key = make_signature_key(agent="/research", intent_class="research_run", signature_key="")
            evt = AgentEvent(
                agent="/research",
                intent=self.context.query,
                intent_class="research_run",
                outcome=outcome,
                signature_key=signature_key,
                confidence=confidence,
                error_type="" if outcome == "SUCCESS" else "quality_or_failures",
                root_cause="" if outcome == "SUCCESS" else "incomplete_or_failed_steps",
                fix="",
                meta={
                    "session_id": self.context.session_id,
                    "output_dir": str(self.context.output_dir) if self.context.output_dir else "",
                    "summary": summary,
                },
            )
            client = get_client()
            client.report_outcome(evt)
        except Exception:
            # 学習基盤の不調で研究本体を落とさない
            return
    
    def _save_phase_output(self, phase: Phase):
        """Phase出力を保存"""
        try:
            def _coerce_record(record_cls, item: Any):
                if not isinstance(item, dict):
                    return item
                fields = getattr(record_cls, "__dataclass_fields__", {})
                allowed = set(fields.keys())
                return record_cls(**{k: v for k, v in item.items() if k in allowed})

            if phase == Phase.WIDE:
                # Phase 1
                records = [_coerce_record(RawClaimRecord, c) for c in self.context.raw_claims]
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_phase1",
                    call=lambda: self.writer.save_phase1(records, self.context.search_log),
                    args={
                        "raw_claims_count": len(records),
                        "search_log_count": len(self.context.search_log),
                    },
                    result_summary=lambda _: {"saved": True},
                )
            
            elif phase == Phase.NORMALIZE:
                # Phase 2
                records = [_coerce_record(NormalizedClaimRecord, c) for c in self.context.normalized_claims]
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_phase2",
                    call=lambda: self.writer.save_phase2(records, self.context.gaps, self.context.sub_questions),
                    args={
                        "normalized_claims_count": len(records),
                        "gaps_count": len(self.context.gaps),
                        "sub_questions_count": len(self.context.sub_questions),
                    },
                    result_summary=lambda _: {"saved": True},
                )
            
            elif phase == Phase.DEEP:
                # Phase 3
                records = []
                for item in self.context.evidence:
                    if not isinstance(item, dict):
                        records.append(item)
                        continue
                    d = dict(item)
                    d.setdefault("evidence_id", generate_evidence_id())
                    d.setdefault("claim_ids", d.get("claim_id") and [d["claim_id"]] or [])
                    records.append(_coerce_record(EvidenceRecord, d))
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_phase3",
                    call=lambda: self.writer.save_phase3(records, self.context.coverage_map),
                    args={
                        "evidence_count": len(records),
                        "coverage_keys": len(self.context.coverage_map),
                    },
                    result_summary=lambda _: {"saved": True},
                )
            
            elif phase == Phase.VERIFY:
                # Phase 3.5
                claim_records = [_coerce_record(VerifiedClaimRecord, c) for c in self.context.verified_claims]
                ce_records = [_coerce_record(CounterevidenceLog, c) for c in self.context.counterevidence_log]
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_phase35",
                    call=lambda: self.writer.save_phase35(claim_records, ce_records),
                    args={
                        "verified_claims_count": len(claim_records),
                        "counterevidence_count": len(ce_records),
                    },
                    result_summary=lambda _: {"saved": True},
                )

            elif phase == Phase.INTEGRATE:
                # Phase 4
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_phase4",
                    call=lambda: self.writer.save_phase4(self.context.final_report or ""),
                    args={
                        "report_chars": len(self.context.final_report or ""),
                    },
                    result_summary=lambda _: {"saved": True},
                )
                audit_pack = {
                    "session_id": self.context.session_id,
                    "query": self.context.query,
                    "created_at": datetime.now().isoformat(),
                    "output_dir": str(self.context.output_dir) if self.context.output_dir else "",
                    "summary": self.context.get_summary(),
                    "report_data": self.context.report_data,
                    "verified_claims": self.context.verified_claims,
                    "counterevidence_log": self.context.counterevidence_log,
                    "evidence": self.context.evidence,
                    "normalized_claims": self.context.normalized_claims,
                    "gaps": self.context.gaps,
                    "sub_questions": self.context.sub_questions,
                    "phase_results": self.context.phase_results,
                    "failures": [e.to_dict() for e in self.context.failures],
                }
                call_tool(
                    self.context,
                    tool_name="artifact_writer.save_audit_pack",
                    call=lambda: self.writer.save_audit_pack(audit_pack),
                    args={
                        "session_id": self.context.session_id,
                        "keys": len(audit_pack),
                    },
                    result_summary=lambda _: {"saved": True},
                )
                try:
                    capsule = build_capsule(audit_pack)
                    call_tool(
                        self.context,
                        tool_name="capsules.append_capsule",
                        call=lambda: append_capsule(capsule),
                        args={"session_id": self.context.session_id},
                        result_summary=lambda _: {"saved": True},
                    )
                except Exception:
                    # 索引生成の失敗で研究本体を落とさない
                    pass
            
            self._log(f"  💾 保存完了")
        except Exception as e:
            self._log(f"  ⚠ 保存エラー: {e}")
            self._event_warn("research_phase_save_error", phase=phase.value, error_message=str(e))
    
    def _create_failure_event(self, result: PhaseResult) -> FailureEvent:
        """PhaseResultから失敗イベントを生成"""
        from .failure_detector import FailureType, _generate_failure_id
        
        return FailureEvent(
            failure_id=_generate_failure_id(FailureType.UNKNOWN, {"phase": result.phase.value}),
            failure_type=FailureType.UNKNOWN,
            timestamp=datetime.now().isoformat(),
            phase=result.phase.value,
            description=result.error or "Unknown error",
            context={"result": result.output},
            severity="error"
        )
    
    def _log(self, message: str):
        """ログ出力"""
        if self.config.verbose:
            print(message)


# 便利関数
def run_research(query: str, **kwargs) -> ResearchRunContext:
    """リサーチを実行するショートカット"""
    orchestrator = ResearchOrchestrator(query, **kwargs)
    return orchestrator.run()
