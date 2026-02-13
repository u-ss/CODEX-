# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Models Module
共通型定義（Stance, ClaimStatus, ArtifactBase）

v4.3.3 変更点:
- ClaimStatus: 単一ソース化（verification.py/termination.py統合）
- Severity: failure_detector用
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4


# Stance型（文字列Enum統一）
Stance = Literal["supports", "refutes", "neutral"]

# ClaimStatus型（v4.3.3: 単一ソース化）
ClaimStatus = Literal["VERIFIED", "CONDITIONED", "CONTESTED", "UNSUPPORTED", "REFUTED"]

# Severity型（failure_detector用）
Severity = Literal["info", "warn", "error", "fatal"]


def stance_to_int(stance: Stance) -> int:
    """
    Stanceを数値に変換（後方互換用）
    
    Args:
        stance: "supports" / "refutes" / "neutral"
    
    Returns:
        +1 (supports), -1 (refutes), 0 (neutral)
    """
    mapping = {"supports": 1, "refutes": -1, "neutral": 0}
    return mapping.get(stance, 0)


def int_to_stance(value: int) -> Stance:
    """
    数値をStanceに変換（旧形式読み込み用）
    
    Args:
        value: +1, -1, 0
    
    Returns:
        "supports" / "refutes" / "neutral"
    """
    if value > 0:
        return "supports"
    elif value < 0:
        return "refutes"
    return "neutral"


@dataclass
class ArtifactBase:
    """
    全Artifactの共通メタ（SKILL.md準拠）
    
    SKILL必須: id, created_at, scope, assumptions, open_questions
    """
    id: str = field(default_factory=lambda: f"art_{uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    scope: Optional[str] = None
    assumptions: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)


def generate_claim_id() -> str:
    """
    ユニークなclaim_id生成（衝突回避）
    """
    return f"clm_{uuid4().hex[:12]}"

def generate_claim_id_from_text(statement: str) -> str:
    """
    statementから安定したclaim_idを生成（重複統合向け）。
    """
    from hashlib import sha256

    s = (statement or "").strip().encode("utf-8")
    if not s:
        return generate_claim_id()
    return f"clm_{sha256(s).hexdigest()[:12]}"


def generate_evidence_id() -> str:
    """
    ユニークなevidence_id生成
    """
    return f"ev_{uuid4().hex[:12]}"
