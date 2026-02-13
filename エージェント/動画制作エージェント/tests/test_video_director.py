from __future__ import annotations

from video_pipeline.director import augment_shot_list_payload, build_prompt_pack, build_quality_report
from video_pipeline.models import ShotList


def _shot_list_payload() -> dict:
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
            "director": {
                "mode": "storyboard",
                "no_paid_api": True,
                "variants_per_shot": 3,
                "continuity_tokens": ["same actor", "same costume"],
            },
        },
        "shots": [
            {"id": "s001", "narration": "city wakes up", "timing": {"min_sec": 2.0, "max_sec": 6.0, "post_pad_sec": 0.3}},
            {"id": "s002", "subtitle": "light paints the street", "timing": {"min_sec": 2.0, "max_sec": 6.0, "post_pad_sec": 0.3}},
        ],
    }


def test_prompt_pack_generates_scene_per_shot() -> None:
    shot_list = ShotList.model_validate(_shot_list_payload())
    pack = build_prompt_pack(shot_list, run_id="r1")
    assert pack.mode == "storyboard"
    assert pack.no_paid_api is True
    assert len(pack.scenes) == 2
    assert all(scene.prompt_main for scene in pack.scenes)
    assert all(len(scene.prompt_variants) == 3 for scene in pack.scenes)


def test_quality_report_flags_paid_api_policy_violation() -> None:
    payload = _shot_list_payload()
    payload["settings"]["director"]["no_paid_api"] = False
    shot_list = ShotList.model_validate(payload)
    pack = build_prompt_pack(shot_list, run_id="r1")
    report = build_quality_report(shot_list, run_id="r1", prompt_pack=pack)
    assert any(finding.code == "PAID_API_DISABLED_POLICY" for finding in report.findings)


def test_quality_report_flags_aspect_ratio_mismatch() -> None:
    payload = _shot_list_payload()
    payload["settings"]["director"]["storyboard_aspect_ratio"] = "9:16"
    payload["settings"]["resolution"] = {"width": 1920, "height": 1080}
    shot_list = ShotList.model_validate(payload)
    pack = build_prompt_pack(shot_list, run_id="r1")
    report = build_quality_report(shot_list, run_id="r1", prompt_pack=pack)
    assert any(
        finding.code == "ASPECT_RATIO_MISMATCH" and finding.severity == "error" for finding in report.findings
    )


def test_augment_preserves_existing_storyboard_when_not_force() -> None:
    payload = _shot_list_payload()
    payload["shots"][0]["video"] = {"storyboard": {"prompt_main": "manual prompt"}}
    shot_list = ShotList.model_validate(payload)
    pack = build_prompt_pack(shot_list, run_id="r1")
    report = build_quality_report(shot_list, run_id="r1", prompt_pack=pack)
    directed = augment_shot_list_payload(payload, pack, report, force=False)
    assert directed["shots"][0]["video"]["storyboard"]["prompt_main"] == "manual prompt"
