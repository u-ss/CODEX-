from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_pipeline.models import ShotList
from video_pipeline.schema import load_schema, validate_with_schema


def _valid_payload() -> dict:
    return {
        "schema_version": "1.0.0",
        "project_slug": "demo",
        "settings": {
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "voicevox": {"base_url": "http://127.0.0.1:50021", "speaker": 1},
            "bgm": {"path": None},
            "look": {},
            "export": {},
        },
        "shots": [
            {"id": "s001", "narration": "hello"},
            {"id": "s002", "narration": "world"},
        ],
    }


def test_schema_and_pydantic_accept_valid_payload() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    schema = load_schema(repo_root / "エージェント" / "動画制作エージェント" / "schemas" / "shot_list.schema.json")
    payload = _valid_payload()
    validate_with_schema(payload, schema)
    shot_list = ShotList.model_validate(payload)
    assert shot_list.project_slug == "demo"
    assert len(shot_list.shots) == 2


def test_schema_accepts_director_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    schema = load_schema(repo_root / "エージェント" / "動画制作エージェント" / "schemas" / "shot_list.schema.json")
    payload = _valid_payload()
    payload["director_artifacts"] = {"sora_quality_report": "path/to/report.json"}
    validate_with_schema(payload, schema)


def test_duplicate_ids_are_rejected() -> None:
    payload = _valid_payload()
    payload["shots"] = [{"id": "s001"}, {"id": "s001"}]
    with pytest.raises(Exception):
        ShotList.model_validate(payload)


def test_invalid_shot_id_pattern_is_rejected() -> None:
    payload = _valid_payload()
    payload["shots"][0]["id"] = "scene-01"
    with pytest.raises(Exception):
        ShotList.model_validate(payload)


def test_schema_requires_core_fields(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload.pop("settings")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(payload), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[3]
    schema = load_schema(repo_root / "エージェント" / "動画制作エージェント" / "schemas" / "shot_list.schema.json")
    with pytest.raises(Exception):
        validate_with_schema(json.loads(raw.read_text(encoding="utf-8")), schema)
