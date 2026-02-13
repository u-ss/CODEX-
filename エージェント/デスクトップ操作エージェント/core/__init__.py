# desktop_control/core/__init__.py
# Desktop Agent Core Module - 統合エクスポート
# ChatGPT 5.2相談（10ラリー）に基づく実装

from .action_contract import (
    Action,
    ActionResult,
    ActionRunner,
    Check,
    PreconditionFailed,
    PostconditionFailed,
    element_exists,
    element_not_exists,
    url_contains,
)

from .trace import (
    Trace,
    TraceReplay,
)

from .conditions import (
    CheckResult,
    Condition,
    AllOf,
    AnyOf,
    DomExists,
    DomNotExists,
    DomVisibleEnabled,
    DomTextContains,
    UrlContains,
    UrlMatches,
    UrlChanged,
    TitleContains,
    UiaExists,
    UiaExistsEnabled,
    NoModalDialog,
)

from .failure_taxonomy import (
    FailType,
    FailureEvent,
    FailureClassifier,
    RECOVERY_PLAYBOOK,
    Tactic,
    backoff_sleep_tactic,
    invalidate_locator_tactic,
    reresolve_locator_tactic,
    reload_page_tactic,
    handle_modal_tactic,
    ask_user_permission_tactic,
)

from .circuit_breaker import (
    CBKey,
    CBState,
    CBRecord,
    Threshold,
    CircuitBreaker,
    DEFAULT_THRESHOLDS,
)

from .safety_guard import (
    RiskLevel,
    DESTRUCTIVE_WORDS,
    risk_from_action,
    risk_heuristic,
    ConfirmRequest,
    HumanConfirmer,
    ConsoleConfirmer,
    SafetyGuardError,
    safety_guard,
    mask_text,
    sanitize_dict,
    redact_type_params,
    safe_trace_log,
)

from .router import (
    Layer,
    Health,
    RouteKey,
    LayerStats,
    MetricsStore,
    HealthChecker,
    Resolver,
    RouteDecision,
    Router,
)

from .executors import (
    Executor,
    ExecError,
    ElementNotFound,
    ActionNotSupported,
    CDPExecutor,
    UIAExecutor,
    PixelExecutor,
)


__all__ = [
    # action_contract
    "Action",
    "ActionResult",
    "ActionRunner",
    "Check",
    "PreconditionFailed",
    "PostconditionFailed",
    "element_exists",
    "element_not_exists",
    "url_contains",
    # trace
    "Trace",
    "TraceReplay",
    # conditions
    "CheckResult",
    "Condition",
    "AllOf",
    "AnyOf",
    "DomExists",
    "DomNotExists",
    "DomVisibleEnabled",
    "DomTextContains",
    "UrlContains",
    "UrlMatches",
    "UrlChanged",
    "TitleContains",
    "UiaExists",
    "UiaExistsEnabled",
    "NoModalDialog",
    # failure_taxonomy
    "FailType",
    "FailureEvent",
    "FailureClassifier",
    "RECOVERY_PLAYBOOK",
    "Tactic",
    "backoff_sleep_tactic",
    "invalidate_locator_tactic",
    "reresolve_locator_tactic",
    "reload_page_tactic",
    "handle_modal_tactic",
    "ask_user_permission_tactic",
    # circuit_breaker
    "CBKey",
    "CBState",
    "CBRecord",
    "Threshold",
    "CircuitBreaker",
    "DEFAULT_THRESHOLDS",
    # safety_guard
    "RiskLevel",
    "DESTRUCTIVE_WORDS",
    "risk_from_action",
    "risk_heuristic",
    "ConfirmRequest",
    "HumanConfirmer",
    "ConsoleConfirmer",
    "SafetyGuardError",
    "safety_guard",
    "mask_text",
    "sanitize_dict",
    "redact_type_params",
    "safe_trace_log",
    # router
    "Layer",
    "Health",
    "RouteKey",
    "LayerStats",
    "MetricsStore",
    "HealthChecker",
    "Resolver",
    "RouteDecision",
    "Router",
    # executors
    "Executor",
    "ExecError",
    "ElementNotFound",
    "ActionNotSupported",
    "CDPExecutor",
    "UIAExecutor",
    "PixelExecutor",
]
