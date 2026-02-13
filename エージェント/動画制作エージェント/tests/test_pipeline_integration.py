from __future__ import annotations

import shutil
import json
from pathlib import Path

import pytest

from video_pipeline.io import run_command
from video_pipeline.models import NarrationEntry, NarrationManifest, ShotList, Timeline
from video_pipeline.paths import PipelinePaths
from video_pipeline.steps import (
    step_d1_direct,
    step_a1_validate,
    step_a2_collect,
    step_a3_probe,
    step_a5_timing,
    step_a6_props,
    step_a8_mix,
    step_a9_finalize,
)


def _make_color_clip(path: Path, *, duration_sec: float, color: str) -> None:
    result = run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=640x360:r=24",
            "-t",
            f"{duration_sec:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe required")
def test_pipeline_partial_integration(tmp_path: Path) -> None:
    repo_root = tmp_path
    project_slug = "demo"
    run_id = "integration"
    paths = PipelinePaths(repo_root=repo_root, project_slug=project_slug, run_id=run_id)
    paths.ensure_runtime_dirs()

    schema_src = Path(__file__).resolve().parents[1] / "schemas" / "shot_list.schema.json"
    schema_dst = repo_root / "エージェント" / "動画制作エージェント" / "schemas" / "shot_list.schema.json"
    schema_dst.parent.mkdir(parents=True, exist_ok=True)
    schema_dst.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")

    shot_list_payload = {
        "schema_version": "1.0.0",
        "project_slug": project_slug,
        "settings": {
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "voicevox": {"base_url": "http://127.0.0.1:50021", "speaker": 1},
            "bgm": {"path": None, "bpm": 120, "offset_sec": 0},
            "look": {},
            "export": {},
            "subtitle_max_chars": 14,
            "beat_snap_max_frames": 8,
        },
        "shots": [
            {"id": "s001", "narration": "a", "timing": {"min_sec": 1.0, "max_sec": 4.0, "post_pad_sec": 0.3}},
            {"id": "s002", "narration": "b", "timing": {"min_sec": 1.0, "max_sec": 4.0, "post_pad_sec": 0.3}},
        ],
    }
    paths.shot_list_path.parent.mkdir(parents=True, exist_ok=True)
    paths.shot_list_path.write_text(json.dumps(shot_list_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    _make_color_clip(paths.sora_inbox_dir / "s001_take.mp4", duration_sec=1.5, color="red")
    _make_color_clip(paths.sora_inbox_dir / "s002_take.mp4", duration_sec=1.6, color="blue")

    step_d1_direct(paths, force=True, dry_run=False)
    step_a1_validate(paths)
    normalized = ShotList.model_validate_json(paths.normalized_shot_list_path.read_text(encoding="utf-8"))
    assert normalized.director_artifacts is not None
    assert normalized.director_artifacts.sora_quality_report is not None
    step_a2_collect(paths, force=True, dry_run=False)
    step_a3_probe(paths, force=True)

    narration_manifest = NarrationManifest(
        project_slug=project_slug,
        run_id=run_id,
        entries=[
            NarrationEntry(shot_id="s001", wav_path=None, duration_sec=0.0),
            NarrationEntry(shot_id="s002", wav_path=None, duration_sec=0.0),
        ],
    )
    paths.narration_manifest_path.write_text(narration_manifest.model_dump_json(indent=2), encoding="utf-8")

    step_a5_timing(paths)
    step_a6_props(paths)

    timeline = Timeline.model_validate_json(paths.timeline_path.read_text(encoding="utf-8"))
    _make_color_clip(paths.draft_path, duration_sec=timeline.total_duration_sec, color="black")

    step_a8_mix(paths)
    step_a9_finalize(paths)
    assert paths.directed_shot_list_path.is_file()
    assert paths.sora_prompt_pack_path.is_file()
    assert paths.sora_style_guide_path.is_file()
    assert paths.sora_quality_report_path.is_file()
    assert paths.final_path.is_file()
