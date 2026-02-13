# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Artifacts Module
Phase間のArtifacts永続化（IO Record専用）

v4.3.3 変更点:
- Record命名に変更（claims.pyのドメインモデルと重複解消）
  - RawClaim → RawClaimRecord
  - NormalizedClaim → NormalizedClaimRecord
  - Evidence → EvidenceRecord
  - VerifiedClaim → VerifiedClaimRecord
- ClaimStatusをmodels.pyから参照
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Literal
import json

from .models import Stance, ClaimStatus

# 出力ディレクトリ
DEFAULT_OUTPUT_DIR = Path("_outputs/research")


@dataclass
class RawClaimRecord:
    """Phase 1 IO: 生の主張レコード（v4.3.3 改名）"""
    claim_text: str
    source_url: str
    published_at: Optional[str] = None
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())
    fingerprint: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedClaimRecord:
    """Phase 2 IO: 正規化された主張レコード（v4.3.3 改名）"""
    claim_id: str
    statement: str
    scope: str
    decision_relevance: Literal["high", "medium", "low"]
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object_: Optional[str] = None
    time_constraint: Optional[str] = None
    source_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["object"] = d.pop("object_")  # Pythonの予約語回避
        return d


@dataclass
class EvidenceRecord:
    """Phase 3 IO: 証拠レコード（v4.3.3 改名）"""
    evidence_id: str
    claim_ids: List[str]
    source_id: str
    url: str
    quote: str
    stance: Stance  # v4.3.3: models.pyから参照
    quality_score: float  # 0.0-1.0
    tier: str = "C"  # 推奨: scoring.py準拠の S/A/B/C/D（後方互換で tier1/tier2/tier3 も許容）
    bias_flags: List[str] = field(default_factory=list)
    freshness: Optional[float] = None
    locator: Optional[str] = None
    extracted_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerifiedClaimRecord:
    """Phase 3.5 IO: 検証済み主張レコード（v4.3.3 改名）"""
    claim_id: str
    statement: str
    status: ClaimStatus  # v4.3.3: models.pyから参照
    rationale: str
    conditions: List[str] = field(default_factory=list)
    supporting_evidence_ids: List[str] = field(default_factory=list)
    refuting_evidence_ids: List[str] = field(default_factory=list)
    relaxation_reason: Optional[str] = None
    verified_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CounterevidenceLog:
    """Phase 3.5 IO: 反証探索ログ"""
    claim_id: str
    search_queries: List[str]
    search_scope: str
    found_counterevidence: bool
    impact_on_status: Optional[str] = None
    searched_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# v4.3.3: 後方互換エイリアス
RawClaim = RawClaimRecord
NormalizedClaim = NormalizedClaimRecord
Evidence = EvidenceRecord
VerifiedClaim = VerifiedClaimRecord


class ArtifactWriter:
    """Artifacts永続化ヘルパー"""
    
    def __init__(self, session_id: Optional[str] = None, output_dir: Optional[Path] = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR / self.session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _write_jsonl(self, filename: str, items: List[Any]):
        """JSONL形式で書き出し"""
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                d = item.to_dict() if hasattr(item, "to_dict") else item
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        return path
    
    def save_phase1(self, raw_claims: List[RawClaimRecord], search_log: List[Dict]) -> Path:
        """Phase 1 出力を保存"""
        self._write_jsonl("raw_claims.jsonl", raw_claims)
        self._write_jsonl("search_log.jsonl", search_log)
        return self.output_dir
    
    def save_phase2(
        self,
        normalized_claims: List[NormalizedClaimRecord],
        gaps: List[Dict],
        sub_questions: List[str]
    ) -> Path:
        """Phase 2 出力を保存"""
        self._write_jsonl("normalized_claims.jsonl", normalized_claims)
        self._write_jsonl("gaps.jsonl", gaps)
        with open(self.output_dir / "sub_questions.json", "w", encoding="utf-8") as f:
            json.dump(sub_questions, f, ensure_ascii=False, indent=2)
        return self.output_dir
    
    def save_phase3(self, evidence: List[EvidenceRecord], coverage_map: Dict) -> Path:
        """Phase 3 出力を保存"""
        self._write_jsonl("evidence.jsonl", evidence)
        with open(self.output_dir / "coverage_map.json", "w", encoding="utf-8") as f:
            json.dump(coverage_map, f, ensure_ascii=False, indent=2)
        return self.output_dir
    
    def save_phase35(
        self,
        verified_claims: List[VerifiedClaimRecord],
        counterevidence_log: List[CounterevidenceLog]
    ) -> Path:
        """Phase 3.5 出力を保存"""
        self._write_jsonl("verified_claims.jsonl", verified_claims)
        self._write_jsonl("counterevidence_log.jsonl", counterevidence_log)
        return self.output_dir

    def save_phase4(self, final_report: str) -> Path:
        """Phase 4 出力（最終レポート）を保存"""
        with open(self.output_dir / "final_report.md", "w", encoding="utf-8") as f:
            f.write(final_report or "")
        return self.output_dir

    def save_audit_pack(self, audit_pack: Dict[str, Any]) -> Path:
        """
        監査/再現用パックを保存。

        目的:
        - Phase 4の文章とは別に、/CODE等で機械的に再利用できる構造化データを残す
        """
        with open(self.output_dir / "audit_pack.json", "w", encoding="utf-8") as f:
            json.dump(audit_pack, f, ensure_ascii=False, indent=2)
        return self.output_dir
    
    def get_output_path(self) -> Path:
        return self.output_dir
