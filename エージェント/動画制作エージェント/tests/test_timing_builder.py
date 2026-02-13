from __future__ import annotations

from pathlib import Path

from video_pipeline.ffmpeg_utils import beat_snap_frame
from video_pipeline.models import (
    MediaProbeEntry,
    MediaProbeManifest,
    NarrationEntry,
    NarrationManifest,
    ShotList,
)
from video_pipeline.paths import PipelinePaths
from video_pipeline.steps import step_a5_timing
from video_pipeline.subtitle import split_subtitle_lines


def test_beat_snap_within_window() -> None:
    snapped = beat_snap_frame(frame=51, fps=24, bpm=120, offset_sec=0.0, max_snap_frames=8)
    assert snapped == 48


def test_subtitle_split_prefers_punctuation() -> None:
    lines = split_subtitle_lines("夜がほどけ、都市が息を吸う。朝が来る。", max_chars=10, max_lines=2)
    assert len(lines) <= 2
    assert lines[0].endswith("、") or lines[0].endswith("。")


def test_timeline_builder_auto_fit(tmp_path: Path) -> None:
    repo_root = tmp_path
    paths = PipelinePaths(repo_root=repo_root, project_slug="demo", run_id="run1")
    paths.ensure_runtime_dirs()

    shot_list = ShotList.model_validate(
        {
            "schema_version": "1.0.0",
            "project_slug": "demo",
            "settings": {
                "fps": 24,
                "resolution": {"width": 1920, "height": 1080},
                "voicevox": {"base_url": "http://127.0.0.1:50021", "speaker": 1},
                "bgm": {"path": None, "bpm": 120, "offset_sec": 0},
                "look": {},
                "export": {},
                "subtitle_max_chars": 12,
                "beat_snap_max_frames": 8,
            },
            "shots": [
                {"id": "s001", "narration": "夜がほどけ、都市が息を吸う。", "timing": {"min_sec": 2.0, "max_sec": 6.0, "post_pad_sec": 0.3}},
                {"id": "s002", "narration": "朝が来る。", "timing": {"min_sec": 2.0, "max_sec": 6.0, "post_pad_sec": 0.3}},
            ],
        }
    )
    paths.normalized_shot_list_path.write_text(shot_list.model_dump_json(indent=2), encoding="utf-8")

    probe = MediaProbeManifest(
        project_slug="demo",
        run_id="run1",
        entries=[
            MediaProbeEntry(shot_id="s001", takes=[], selected_take="a", render_src="C:/tmp/s001.mp4", duration_sec=2.1, notes=[]),
            MediaProbeEntry(shot_id="s002", takes=[], selected_take="b", render_src="C:/tmp/s002.mp4", duration_sec=1.9, notes=[]),
        ],
    )
    paths.media_probe_path.write_text(probe.model_dump_json(indent=2), encoding="utf-8")

    narration = NarrationManifest(
        project_slug="demo",
        run_id="run1",
        entries=[
            NarrationEntry(shot_id="s001", wav_path="C:/tmp/s001.wav", duration_sec=1.6),
            NarrationEntry(shot_id="s002", wav_path="C:/tmp/s002.wav", duration_sec=1.0),
        ],
    )
    paths.narration_manifest_path.write_text(narration.model_dump_json(indent=2), encoding="utf-8")

    result = step_a5_timing(paths)
    assert "timeline.json" in result.artifacts
    payload = paths.timeline_path.read_text(encoding="utf-8")
    assert "total_frames" in payload
