# Desktop Control v5.0.0 Core
from .failure_event import (
    FailureKind, ReasonCode, FailureEvent, FailureEvidence,
    create_failure_event, classify_failure, get_failure_weight
)
from .wait_heartbeat import (
    ProgressHeartbeat, HeartbeatWaitConfig, HeartbeatResult,
    heartbeat_wait, wrap_length_sample, wrap_hash_sample
)
from .action_spec import (
    ActionSpec, VerifyCheck, WaitSpec, RiskSpec,
    RiskLevel, VerifyType, WaitKind,
    navigate_action, fill_action, click_action, press_action, wait_action,
)
from .planner import (
    Planner, Intent, Observation, PlanResult,
    IntentVerb, TargetApp,
    extract_intent, plan_simple,
    # 学習機能
    TemplateStore, LearnedTemplate,
    get_template_store, learn_from_actions,
)
from .executor import (
    Executor, ExecutionResult,
    execute_instruction,
)
