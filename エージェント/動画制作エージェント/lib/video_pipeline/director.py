from __future__ import annotations

import copy
import re
from typing import Any

from .models import (
    DirectorQualityReport,
    PromptVariant,
    QualityFinding,
    ShotList,
    ShotSpec,
    SoraPromptPack,
    StoryboardPrompt,
)

FOCUS_PRESETS = [
    ("v1", "master framing"),
    ("v2", "subject detail"),
    ("v3", "environment atmosphere"),
    ("v4", "camera motion"),
    ("v5", "light and texture"),
]

ASPECT_RATIO_MAP = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "21:9": 21 / 9,
}


def _compact_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_tokens(tokens: list[str]) -> list[str]:
    cleaned: list[str] = []
    for token in tokens:
        normalized = _compact_text(token)
        if not normalized:
            continue
        if normalized.lower() in {item.lower() for item in cleaned}:
            continue
        cleaned.append(normalized)
    return cleaned


def _scene_subject(shot: ShotSpec) -> str:
    candidates = [
        _compact_text(shot.narration),
        _compact_text(shot.subtitle),
    ]
    for item in candidates:
        if item:
            return item
    return f"Visual beat for {shot.id}"


def _scene_duration_sec(shot: ShotSpec, default_duration: float) -> float:
    midpoint = (shot.timing.min_sec + shot.timing.max_sec) / 2.0
    preferred = max(default_duration, midpoint)
    return max(shot.timing.min_sec, min(preferred, shot.timing.max_sec))


def _build_main_prompt(
    *,
    subject: str,
    shot: ShotSpec,
    style_keywords: list[str],
    continuity_tokens: list[str],
    fps: int,
    aspect_ratio: str,
) -> str:
    camera_hint = "steady cinematic camera, controlled motion"
    transition_hint = f"end transition: {shot.transition_to_next.type}"
    style = ", ".join(style_keywords) if style_keywords else "cinematic"
    continuity = ", ".join(continuity_tokens) if continuity_tokens else "same character identity"
    return (
        f"{subject}. {camera_hint}. style: {style}. continuity: {continuity}. "
        f"technical: {fps}fps, aspect {aspect_ratio}, single coherent shot, clean composition. {transition_hint}."
    )


def _build_variants(main_prompt: str, variants_per_shot: int) -> list[PromptVariant]:
    variants: list[PromptVariant] = []
    for idx in range(variants_per_shot):
        preset_id, focus = FOCUS_PRESETS[idx % len(FOCUS_PRESETS)]
        variants.append(
            PromptVariant(
                id=preset_id,
                focus=focus,
                prompt=f"{main_prompt} Priority: {focus}. Avoid abrupt changes.",
            )
        )
    return variants


def _quality_checks(shot: ShotSpec) -> list[str]:
    checks = [
        "Keep main subject identity stable across the full scene.",
        "Avoid flicker, broken geometry, and sudden camera teleport.",
        "Preserve readable foreground/background separation.",
    ]
    if shot.transition_to_next.type == "fade":
        checks.append("Finish with smooth brightness ramp for fade transition.")
    else:
        checks.append("Finish on a clean frame for hard cut transition.")
    return checks


def _aspect_ratio_mismatch(width: int, height: int, aspect_ratio: str, tolerance: float = 0.03) -> bool:
    if height <= 0:
        return False
    expected = ASPECT_RATIO_MAP.get(aspect_ratio)
    if expected is None:
        return False
    actual = width / height
    return abs(actual - expected) / expected > tolerance


def build_prompt_pack(shot_list: ShotList, run_id: str) -> SoraPromptPack:
    director = shot_list.settings.director
    style_keywords = _sanitize_tokens(director.style_keywords)
    continuity_tokens = _sanitize_tokens(director.continuity_tokens)
    if not continuity_tokens:
        continuity_tokens = [
            f"project:{shot_list.project_slug}",
            "consistent wardrobe",
            "stable face identity",
        ]

    scenes: list[StoryboardPrompt] = []
    for scene_index, shot in enumerate(shot_list.shots, start=1):
        subject = _scene_subject(shot)
        duration_sec = _scene_duration_sec(shot, director.storyboard_default_duration_sec)
        prompt_main = _build_main_prompt(
            subject=subject,
            shot=shot,
            style_keywords=style_keywords,
            continuity_tokens=continuity_tokens,
            fps=shot_list.settings.fps,
            aspect_ratio=director.storyboard_aspect_ratio,
        )
        scenes.append(
            StoryboardPrompt(
                shot_id=shot.id,
                scene_index=scene_index,
                target_duration_sec=duration_sec,
                aspect_ratio=director.storyboard_aspect_ratio,
                prompt_main=prompt_main,
                negative_prompt=director.negative_prompt_base,
                style_instructions=style_keywords,
                quality_checks=_quality_checks(shot),
                prompt_variants=_build_variants(prompt_main, director.variants_per_shot),
            )
        )

    return SoraPromptPack(
        project_slug=shot_list.project_slug,
        run_id=run_id,
        no_paid_api=director.no_paid_api,
        scenes=scenes,
    )


def build_quality_report(shot_list: ShotList, run_id: str, prompt_pack: SoraPromptPack) -> DirectorQualityReport:
    findings: list[QualityFinding] = []
    if not shot_list.settings.director.no_paid_api:
        findings.append(
            QualityFinding(
                shot_id=None,
                severity="error",
                code="PAID_API_DISABLED_POLICY",
                message="director.no_paid_api must stay true for this repository policy.",
                recommendation="Set settings.director.no_paid_api to true.",
            )
        )

    resolution = shot_list.settings.resolution
    aspect_ratio = shot_list.settings.director.storyboard_aspect_ratio
    if _aspect_ratio_mismatch(resolution.width, resolution.height, aspect_ratio):
        findings.append(
            QualityFinding(
                shot_id=None,
                severity="error",
                code="ASPECT_RATIO_MISMATCH",
                message=(
                    f"Resolution {resolution.width}x{resolution.height} does not match storyboard "
                    f"aspect ratio {aspect_ratio}."
                ),
                recommendation="Align settings.resolution with director.storyboard_aspect_ratio.",
            )
        )

    duplicate_story_subjects: dict[str, list[str]] = {}
    for shot in shot_list.shots:
        subject = _scene_subject(shot).lower()
        duplicate_story_subjects.setdefault(subject, []).append(shot.id)
        if not _compact_text(shot.narration) and not _compact_text(shot.subtitle):
            findings.append(
                QualityFinding(
                    shot_id=shot.id,
                    severity="warn",
                    code="MISSING_TEXT_BRIEF",
                    message="Shot has no narration/subtitle seed text for prompt intent.",
                    recommendation="Add narration or subtitle to improve prompt specificity.",
                )
            )
        if shot.timing.max_sec - shot.timing.min_sec < 0.8:
            findings.append(
                QualityFinding(
                    shot_id=shot.id,
                    severity="warn",
                    code="TIMING_RANGE_TIGHT",
                    message="Timing range is narrow and may block beat-sync adjustments.",
                    recommendation="Increase max_sec or reduce min_sec spread.",
                )
            )
        if not _compact_text(shot.video.inbox_match):
            findings.append(
                QualityFinding(
                    shot_id=shot.id,
                    severity="info",
                    code="INBOX_MATCH_IMPLICIT",
                    message="inbox_match is implicit (falls back to shot id).",
                    recommendation="Set explicit inbox_match when file naming may vary.",
                )
            )

    for subject, shot_ids in duplicate_story_subjects.items():
        if subject and len(shot_ids) > 1:
            findings.append(
                QualityFinding(
                    shot_id=None,
                    severity="info",
                    code="SUBJECT_REPETITION",
                    message=f"Multiple shots share very similar subject text: {', '.join(shot_ids)}",
                    recommendation="Differentiate scene intent per shot to avoid repetitive outputs.",
                )
            )

    if not prompt_pack.scenes:
        findings.append(
            QualityFinding(
                shot_id=None,
                severity="error",
                code="NO_SCENES_GENERATED",
                message="Prompt pack has no storyboard scenes.",
                recommendation="Check shot_list.shots and rerun director.",
            )
        )

    return DirectorQualityReport(project_slug=shot_list.project_slug, run_id=run_id, findings=findings)


def augment_shot_list_payload(
    raw_payload: dict[str, Any],
    prompt_pack: SoraPromptPack,
    quality_report: DirectorQualityReport,
    *,
    force: bool,
) -> dict[str, Any]:
    directed = copy.deepcopy(raw_payload)
    shots = directed.get("shots")
    if not isinstance(shots, list):
        return directed

    scene_map = {scene.shot_id: scene for scene in prompt_pack.scenes}
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        shot_id = str(shot.get("id", "")).strip().lower()
        scene = scene_map.get(shot_id)
        if scene is None:
            continue
        video_payload = shot.get("video")
        if not isinstance(video_payload, dict):
            video_payload = {}
            shot["video"] = video_payload

        generated_storyboard = {
            "scene_index": scene.scene_index,
            "target_duration_sec": scene.target_duration_sec,
            "aspect_ratio": scene.aspect_ratio,
            "prompt_main": scene.prompt_main,
            "negative_prompt": scene.negative_prompt,
            "style_instructions": scene.style_instructions,
            "quality_checks": scene.quality_checks,
            "prompt_variants": [item.model_dump(mode="json") for item in scene.prompt_variants],
        }

        existing_storyboard = video_payload.get("storyboard")
        if not force and isinstance(existing_storyboard, dict):
            merged_storyboard = dict(generated_storyboard)
            merged_storyboard.update(existing_storyboard)
            video_payload["storyboard"] = merged_storyboard
        else:
            video_payload["storyboard"] = generated_storyboard

    meta = {
        "generator": "VideoDirector v1 (local-template)",
        "no_paid_api": prompt_pack.no_paid_api,
        "mode": prompt_pack.mode,
        "scene_count": len(prompt_pack.scenes),
        "quality_findings": len(quality_report.findings),
    }
    if not force and isinstance(directed.get("video_director_v1"), dict):
        merged_meta = dict(meta)
        merged_meta.update(directed["video_director_v1"])
        directed["video_director_v1"] = merged_meta
    else:
        directed["video_director_v1"] = meta
    return directed


def render_style_guide_markdown(prompt_pack: SoraPromptPack, quality_report: DirectorQualityReport) -> str:
    lines: list[str] = []
    lines.append(f"# Sora Storyboard Guide ({prompt_pack.project_slug})")
    lines.append("")
    lines.append("This pack is generated locally. No paid API calls are used.")
    lines.append("")
    lines.append("## Global Rules")
    lines.append("- Keep one coherent subject identity per shot.")
    lines.append("- Use Storyboard mode and configure one scene per shot.")
    lines.append("- Generate 2-3 takes per scene, then keep best takes for A2.")
    lines.append("- Export filenames should include the shot_id (example: s001_take01.mp4).")
    lines.append("- If filenames vary, set video.inbox_match to a stable token and include it in the filename.")
    lines.append("")
    lines.append("## Scene Prompts")
    for scene in prompt_pack.scenes:
        lines.append(f"### {scene.shot_id} (scene {scene.scene_index})")
        lines.append(f"- Duration target: {scene.target_duration_sec:.2f}s")
        lines.append(f"- Aspect ratio: {scene.aspect_ratio}")
        lines.append("- Main prompt:")
        lines.append(f"  {scene.prompt_main}")
        lines.append("- Negative prompt:")
        lines.append(f"  {scene.negative_prompt}")
        lines.append("- Variants:")
        for variant in scene.prompt_variants:
            lines.append(f"  - {variant.id} ({variant.focus}): {variant.prompt}")
        lines.append("")

    if quality_report.findings:
        lines.append("## Quality Findings")
        for finding in quality_report.findings:
            scope = finding.shot_id or "global"
            lines.append(f"- [{finding.severity}] {scope} {finding.code}: {finding.message}")
    else:
        lines.append("## Quality Findings")
        lines.append("- No blocking findings.")
    lines.append("")
    return "\n".join(lines)
