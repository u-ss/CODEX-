from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from video_pipeline.models import RunState


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "video_pipeline.py"
    spec = importlib.util.spec_from_file_location("video_pipeline_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load script module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_selected_steps_range() -> None:
    module = _load_script_module()
    selected = module.resolve_selected_steps("a2_collect", "a5_timing")
    assert selected == ["a2_collect", "a3_probe", "a4_tts", "a5_timing"]


def test_should_skip_uses_resume_and_success() -> None:
    module = _load_script_module()
    state = RunState(project_slug="demo", run_id="r1")
    state.steps["a1_validate"].status = "success"
    assert module.should_skip(state, "a1_validate", resume=True, force=False) is True
    assert module.should_skip(state, "a1_validate", resume=False, force=False) is False
    assert module.should_skip(state, "a1_validate", resume=True, force=True) is False


def test_load_director_quality_errors_detects_error(tmp_path: Path) -> None:
    module = _load_script_module()
    report_path = tmp_path / "sora_quality_report.json"
    report_path.write_text(
        json.dumps(
            {
                "project_slug": "demo",
                "run_id": "r1",
                "findings": [
                    {"shot_id": None, "severity": "error", "code": "ASPECT_RATIO_MISMATCH", "message": "mismatch"}
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    errors = module.load_director_quality_errors(report_path)
    assert errors == ["ASPECT_RATIO_MISMATCH"]
