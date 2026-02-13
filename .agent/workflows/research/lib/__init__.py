# Research Agent v4.3.3 Library

# 型定義
from .models import (
    Stance, ClaimStatus, Severity, ArtifactBase,
    stance_to_int, int_to_stance, generate_claim_id, generate_evidence_id
)

# ドメインモデル
from .claims import RawClaim, NormalizedClaim, ClaimLink, ClaimNormalizer, Source, Slot
from .scoring import Evidence, ScoredEvidence, ConfidenceResult, freshness_score, authority_score, aggregate_confidence
from .verification import VerifiedClaim, ClaimStatusEnum, ClaimType, Issue, CounterEvidence, RequiredAction, determine_status, is_contested

# 終了条件・失敗検知
from .termination import TerminationConfig, TerminationState, TerminationResult, RoundSnapshot, ClaimSnapshot, should_stop, Status
from .failure_detector import FailureType, FailureEvent, FailureDetector, RecoveryAction, check_verification_failures

# IO・永続化
from .artifacts import (
    ArtifactWriter, CounterevidenceLog,
    RawClaimRecord, NormalizedClaimRecord, EvidenceRecord, VerifiedClaimRecord
)

# v4.3.3: 実行エンジン（筋肉）
from .context import ResearchRunContext
from .phase_runner import PhaseRunner, Phase, PhaseSignal, PhaseResult, stub_handler
from .orchestrator import ResearchOrchestrator, OrchestratorConfig, run_research
