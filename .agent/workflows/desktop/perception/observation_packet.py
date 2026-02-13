# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - Observation Packet
DOM/UIA/ROIハッシュを統合した観測パケット
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import uuid

from .img_hash import ImageHashes


@dataclass(frozen=True)
class ObservationPacket:
    """観測パケット（1観測の全情報）"""
    packet_id: str
    ts_ms: int
    trace_id: str
    step_id: int
    app_id: str
    screen_family: str
    screen_key: Optional[str]
    dom_fingerprint: Optional[str]
    uia_fingerprint: Optional[str]
    roi_hashes: dict[str, ImageHashes]
    confidence: float  # 0.0 - 1.0


def create_observation_packet(
    *,
    trace_id: str,
    step_id: int,
    app_id: str,
    screen_family: str,
    screen_key: Optional[str] = None,
    dom_fingerprint: Optional[str] = None,
    uia_fingerprint: Optional[str] = None,
    roi_hashes: Optional[dict[str, ImageHashes]] = None,
    confidence: float = 1.0,
) -> ObservationPacket:
    """観測パケット作成"""
    return ObservationPacket(
        packet_id=str(uuid.uuid4()),
        ts_ms=int(time.time() * 1000),
        trace_id=trace_id,
        step_id=step_id,
        app_id=app_id,
        screen_family=screen_family,
        screen_key=screen_key,
        dom_fingerprint=dom_fingerprint,
        uia_fingerprint=uia_fingerprint,
        roi_hashes=roi_hashes or {},
        confidence=confidence,
    )


@dataclass(frozen=True)
class ScreenshotPolicyConfig:
    """SS撮影ポリシー設定"""
    hash_change_threshold: int = 12
    min_confidence_to_skip_ss: float = 0.75
    take_ss_on_contradiction: bool = True
    ss_cooldown_ms: int = 2000
    vlm_escalate_only_if: tuple[str, ...] = ("unknown_modal", "ambiguous_state")


def should_take_screenshot(
    prev: Optional[ObservationPacket],
    cur: ObservationPacket,
    cfg: ScreenshotPolicyConfig,
) -> bool:
    """
    SSを撮るべきか判定
    
    条件:
    - 確信度が低い
    - DOM/UIAが矛盾
    - ROIハッシュが大きく変化
    """
    # 確信度チェック
    if cur.confidence < cfg.min_confidence_to_skip_ss:
        return True
    
    # 初回
    if prev is None:
        return True
    
    # screen_family変化
    if prev.screen_family != cur.screen_family:
        return True
    
    # DOM/UIA矛盾（片方だけ変化）
    dom_changed = prev.dom_fingerprint != cur.dom_fingerprint
    uia_changed = prev.uia_fingerprint != cur.uia_fingerprint
    if cfg.take_ss_on_contradiction and (dom_changed != uia_changed):
        return True
    
    # ROIハッシュ変化
    from .img_hash import diff_hash
    for name, cur_hash in cur.roi_hashes.items():
        prev_hash = prev.roi_hashes.get(name)
        if prev_hash is None:
            continue
        distance = diff_hash(prev_hash.dhash or "", cur_hash.dhash or "")
        if distance > cfg.hash_change_threshold:
            return True
    
    return False


def should_escalate_to_vlm(
    cur: ObservationPacket,
    condition: str,
    cfg: ScreenshotPolicyConfig,
) -> bool:
    """VLMにエスカレートすべきか"""
    if condition in cfg.vlm_escalate_only_if:
        return True
    if cur.confidence < 0.5:
        return True
    return False
