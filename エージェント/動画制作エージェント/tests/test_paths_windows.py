from __future__ import annotations

from pathlib import Path

from video_pipeline.paths import PipelinePaths, normalize_path_str


def test_pipeline_paths_layout(tmp_path: Path) -> None:
    paths = PipelinePaths(repo_root=tmp_path, project_slug="demo", run_id="run123")
    assert paths.shot_list_path == tmp_path / "projects" / "demo" / "shot_list.json"
    assert paths.output_dir == tmp_path / "_outputs" / "video_pipeline" / "demo" / "run123"
    assert paths.final_path == tmp_path / "_outputs" / "video_pipeline" / "demo" / "run123" / "exports" / "final.mp4"


def test_normalize_path_uses_forward_slashes(tmp_path: Path) -> None:
    nested = tmp_path / "alpha" / "beta" / "file.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("x", encoding="utf-8")
    normalized = normalize_path_str(nested)
    assert "/" in normalized
    assert "\\" not in normalized
