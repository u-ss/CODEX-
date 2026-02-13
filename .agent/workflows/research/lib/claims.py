# -*- coding: utf-8 -*-
"""
Research Agent v4.3.2 - Claims Module
Claim正規化のためのデータ構造とロジック

v4.3.2 変更点:
- generate_claim_id: 空スロット時はuuid fallback（衝突回避）
- add_raw_claim: slotsを破壊的変更しない（copy後に編集）
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from hashlib import sha256
from uuid import uuid4
import json
import copy


@dataclass
class Source:
    """情報ソース"""
    url: str
    publisher: Optional[str] = None
    published_at: Optional[datetime] = None
    retrieved_at: datetime = field(default_factory=datetime.now)


@dataclass
class RawClaim:
    """生の主張（抽出直後の形式）"""
    raw_claim_id: str
    text: str
    source: Source
    context_span: Optional[str] = None
    language: str = "ja"
    
    @property
    def quote_hash(self) -> str:
        """引用のハッシュ"""
        return sha256(self.text.encode('utf-8')).hexdigest()[:16]


@dataclass
class Slot:
    """主張のスロット（主語/述語/目的語等）"""
    # 主語（誰が/何が）
    subject: List[Dict[str, Any]] = field(default_factory=list)
    # 述語（何をする）
    predicate: Optional[str] = None
    # 目的語（何を）
    object: List[Dict[str, Any]] = field(default_factory=list)
    # 修飾子
    qualifiers: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedClaim:
    """正規化された主張"""
    claim_id: str
    canonical_text: str
    slots: Slot
    claim_type: str = "FACT"  # FACT/FORECAST/INFERENCE/OPINION/POLICY
    polarity: str = "POSITIVE"  # POSITIVE/NEGATIVE
    modality: str = "ASSERTED"  # ASSERTED/POSSIBLE/PLANNED
    scope: Optional[str] = None
    created_from: List[str] = field(default_factory=list)  # raw_claim_ids
    version: int = 1
    
    @classmethod
    def generate_claim_id(cls, slots: Slot) -> str:
        """スロットからclaim_idを生成（fingerprintベース）"""
        # fingerprintを構築
        parts = []
        
        # subject
        if slots.subject:
            sub_ids = [s.get('id', s.get('name', '')) for s in slots.subject]
            parts.append(f"sub={','.join(sorted(sub_ids))}")
        
        # predicate
        if slots.predicate:
            parts.append(f"pred={slots.predicate}")
        
        # object
        if slots.object:
            obj_ids = [o.get('id', o.get('name', '')) for o in slots.object]
            parts.append(f"obj={','.join(sorted(obj_ids))}")
        
        # time qualifier
        if 'time' in slots.qualifiers:
            time_val = slots.qualifiers['time']
            if isinstance(time_val, dict):
                time_str = f"{time_val.get('type', '')}:{time_val.get('value', '')}"
            else:
                time_str = str(time_val)
            parts.append(f"time={time_str}")
        
        fingerprint = "|".join(parts)
        
        # v4.3.2: 空スロット時はfingerprintが空になるためuuid fallback
        if not fingerprint:
            return f"clm_{uuid4().hex[:12]}"
        
        return f"clm_{sha256(fingerprint.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class ClaimLink:
    """RawClaim → NormalizedClaim の紐づけ"""
    raw_claim_id: str
    claim_id: str
    match_method: str = "hybrid"  # hybrid/exact/semantic
    match_score: float = 1.0
    match_reason: List[str] = field(default_factory=list)


class ClaimNormalizer:
    """Claim正規化エンジン"""
    
    # 述語の正規化辞書
    PREDICATE_SYNONYMS = {
        # 発表・リリース系
        "発売": "launch", "投入": "launch", "リリース": "launch",
        "発表": "announce", "公開": "announce",
        # 増減系
        "増加": "increase", "上昇": "increase", "成長": "increase",
        "減少": "decrease", "低下": "decrease", "下落": "decrease",
        # 開始・終了系
        "開始": "start", "始める": "start",
        "終了": "end", "停止": "stop", "中止": "stop",
        # 獲得・達成系
        "獲得": "acquire", "達成": "achieve", "達する": "reach",
    }
    
    def __init__(self):
        self.claims: Dict[str, NormalizedClaim] = {}
        self.links: List[ClaimLink] = []
    
    def normalize_predicate(self, predicate: str) -> str:
        """述語を正規化"""
        return self.PREDICATE_SYNONYMS.get(predicate, predicate.lower())
    
    def add_raw_claim(self, raw: RawClaim, slots: Slot) -> NormalizedClaim:
        """RawClaimを正規化して追加（v4.3.2: 非破壊的）"""
        # v4.3.2: slotsをコピーして破壊的変更を回避
        slots_copy = copy.deepcopy(slots)
        
        # 述語を正規化
        if slots_copy.predicate:
            slots_copy.predicate = self.normalize_predicate(slots_copy.predicate)
        
        # claim_idを生成
        claim_id = NormalizedClaim.generate_claim_id(slots_copy)
        
        # 既存のclaimがあればマージ
        if claim_id in self.claims:
            existing = self.claims[claim_id]
            if raw.raw_claim_id not in existing.created_from:
                existing.created_from.append(raw.raw_claim_id)
                existing.version += 1
            normalized = existing
        else:
            # 新規作成
            normalized = NormalizedClaim(
                claim_id=claim_id,
                canonical_text=raw.text,  # 後でLLMで正規化テキストを生成
                slots=slots_copy,  # v4.3.2: コピーを使用
                created_from=[raw.raw_claim_id]
            )
            self.claims[claim_id] = normalized
        
        # リンクを追加
        link = ClaimLink(
            raw_claim_id=raw.raw_claim_id,
            claim_id=claim_id,
            match_reason=["slot_match"]
        )
        self.links.append(link)
        
        return normalized
    
    def get_claim(self, claim_id: str) -> Optional[NormalizedClaim]:
        """claim_idからClaimを取得"""
        return self.claims.get(claim_id)
    
    def get_all_claims(self) -> List[NormalizedClaim]:
        """全てのNormalizedClaimを取得"""
        return list(self.claims.values())
    
    def to_dict(self) -> Dict[str, Any]:
        """辞書形式でエクスポート"""
        return {
            "claims": {
                cid: {
                    "claim_id": c.claim_id,
                    "canonical_text": c.canonical_text,
                    "claim_type": c.claim_type,
                    "polarity": c.polarity,
                    "modality": c.modality,
                    "created_from": c.created_from,
                }
                for cid, c in self.claims.items()
            },
            "links": [
                {
                    "raw_claim_id": l.raw_claim_id,
                    "claim_id": l.claim_id,
                    "match_score": l.match_score,
                }
                for l in self.links
            ]
        }
