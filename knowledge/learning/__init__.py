from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Optional

from .client import AgentEvent, LearningClient, get_client


@dataclasses.dataclass
class TargetSnapshot:
    data: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class EnvSnapshot:
    data: dict[str, Any] = dataclasses.field(default_factory=dict)


def make_signature_key(
    *,
    agent: str = "",
    intent_class: str = "",
    signature_key: str = "",
    target: Optional[TargetSnapshot] = None,
    env: Optional[EnvSnapshot] = None,
) -> str:
    # Minimal stable signature helper. Prefer explicit signature_key when provided.
    if signature_key:
        return signature_key
    base = f"{agent}|{intent_class}|{target and target.data}|{env and env.data}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "AgentEvent",
    "LearningClient",
    "get_client",
    "TargetSnapshot",
    "EnvSnapshot",
    "make_signature_key",
]
