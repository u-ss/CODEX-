# Perception v5.0.0
from .img_hash import (
    ROI, ImageHashes, compute_dhash_simple, compute_simple_hash,
    diff_hash, is_similar, compare_roi_hashes, has_any_change, all_stable
)
from .observation_packet import (
    ObservationPacket, create_observation_packet,
    ScreenshotPolicyConfig, should_take_screenshot, should_escalate_to_vlm
)
