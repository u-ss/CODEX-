from __future__ import annotations

from video_pipeline.ffmpeg_utils import score_take


def test_take_scoring_prefers_higher_quality_clip() -> None:
    high_score, _ = score_take(
        width=1920,
        height=1080,
        duration_sec=3.0,
        target_duration_sec=3.0,
        bit_rate=8_500_000,
        brightness_mean=126.0,
        freeze_ratio=0.05,
    )
    low_score, _ = score_take(
        width=640,
        height=360,
        duration_sec=0.9,
        target_duration_sec=3.0,
        bit_rate=400_000,
        brightness_mean=20.0,
        freeze_ratio=0.95,
    )
    assert high_score > low_score


def test_take_scoring_detail_has_all_keys() -> None:
    _, detail = score_take(
        width=1280,
        height=720,
        duration_sec=2.0,
        target_duration_sec=2.0,
        bit_rate=2_000_000,
        brightness_mean=128.0,
        freeze_ratio=0.1,
    )
    assert set(detail.keys()) == {"resolution", "duration_fit", "brightness", "bitrate", "motion"}
