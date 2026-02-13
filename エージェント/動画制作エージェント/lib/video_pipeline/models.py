from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import DEFAULT_SCHEMA_VERSION, STEP_IDS


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Resolution(BaseModel):
    width: int = Field(default=1920, ge=16, le=8192)
    height: int = Field(default=1080, ge=16, le=8192)


class VoiceVoxSettings(BaseModel):
    base_url: str = "http://127.0.0.1:50021"
    speaker: int = Field(default=1, ge=0)
    speedScale: float = Field(default=1.0, ge=0.5, le=2.0)
    pitchScale: float = Field(default=0.0, ge=-0.3, le=0.3)
    intonationScale: float = Field(default=1.0, ge=0.0, le=2.0)
    volumeScale: float = Field(default=1.0, ge=0.1, le=4.0)
    prePhonemeLength: float = Field(default=0.0, ge=0.0, le=1.0)
    postPhonemeLength: float = Field(default=0.05, ge=0.0, le=1.0)


class BgmSettings(BaseModel):
    path: str | None = None
    volume_db: float = Field(default=-16.0, ge=-60.0, le=12.0)
    fade_in_sec: float = Field(default=0.35, ge=0.0, le=10.0)
    fade_out_sec: float = Field(default=0.35, ge=0.0, le=10.0)
    bpm: float = Field(default=120.0, ge=40.0, le=240.0)
    offset_sec: float = Field(default=0.0, ge=0.0, le=10.0)
    ducking_enabled: bool = True
    duck_threshold: float = Field(default=0.03, ge=0.001, le=0.8)
    duck_ratio: float = Field(default=8.0, ge=1.0, le=30.0)


class LookSettings(BaseModel):
    lut_path: str | None = None
    contrast: float = Field(default=1.03, ge=0.7, le=1.4)
    saturation: float = Field(default=1.05, ge=0.7, le=1.6)
    brightness: float = Field(default=0.0, ge=-0.2, le=0.2)
    gamma: float = Field(default=1.0, ge=0.7, le=1.6)


class ExportSettings(BaseModel):
    target_lufs: float = Field(default=-14.0, ge=-30.0, le=-5.0)
    truepeak_db: float = Field(default=-1.0, ge=-6.0, le=0.0)


class DirectorSettings(BaseModel):
    mode: Literal["storyboard"] = "storyboard"
    no_paid_api: bool = True
    variants_per_shot: int = Field(default=3, ge=1, le=5)
    storyboard_default_duration_sec: float = Field(default=6.0, ge=1.0, le=20.0)
    storyboard_aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "21:9"] = "16:9"
    continuity_tokens: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(
        default_factory=lambda: ["cinematic", "natural light", "high detail", "stable identity"]
    )
    negative_prompt_base: str = (
        "low quality, blurry, heavy artifacts, watermark, logo, text overlay, frame flicker, identity drift"
    )


class TimingSettings(BaseModel):
    min_sec: float = Field(default=2.0, ge=0.25, le=120.0)
    max_sec: float = Field(default=12.0, ge=0.5, le=300.0)
    post_pad_sec: float = Field(default=0.3, ge=0.0, le=5.0)

    @model_validator(mode="after")
    def validate_min_max(self) -> "TimingSettings":
        if self.min_sec > self.max_sec:
            raise ValueError("timing.min_sec must be <= timing.max_sec")
        return self


class VideoSpec(BaseModel):
    inbox_match: str | None = None


class TransitionSpec(BaseModel):
    type: Literal["cut", "fade"] = "cut"
    duration_frames: int = Field(default=6, ge=0, le=96)


class ShotSpec(BaseModel):
    id: str
    narration: str | None = None
    subtitle: str | None = None
    video: VideoSpec = Field(default_factory=VideoSpec)
    timing: TimingSettings = Field(default_factory=TimingSettings)
    transition_to_next: TransitionSpec = Field(default_factory=TransitionSpec)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip().lower()
        if not text.startswith("s") or not text[1:].isdigit() or len(text) < 4:
            raise ValueError("shot.id must match s### pattern")
        return text


class PipelineSettings(BaseModel):
    fps: int = Field(default=24, ge=12, le=120)
    resolution: Resolution = Field(default_factory=Resolution)
    inbox_dir: str = "sora_inbox"
    media_dir: str = "media"
    voicevox: VoiceVoxSettings = Field(default_factory=VoiceVoxSettings)
    bgm: BgmSettings = Field(default_factory=BgmSettings)
    look: LookSettings = Field(default_factory=LookSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    director: DirectorSettings = Field(default_factory=DirectorSettings)
    subtitle_max_chars: int = Field(default=18, ge=6, le=40)
    target_duration_sec: float | None = Field(default=None, ge=1.0, le=7200.0)
    beat_snap_max_frames: int = Field(default=8, ge=0, le=24)


class DirectorArtifacts(BaseModel):
    run_id: str | None = None
    shot_list_directed: str | None = None
    sora_prompt_pack: str | None = None
    sora_style_guide: str | None = None
    sora_quality_report: str | None = None


class ShotList(BaseModel):
    schema_version: str = DEFAULT_SCHEMA_VERSION
    project_slug: str
    settings: PipelineSettings = Field(default_factory=PipelineSettings)
    director_artifacts: DirectorArtifacts | None = None
    shots: list[ShotSpec] = Field(default_factory=list, min_length=1)

    @field_validator("project_slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        slug = value.strip().lower().replace(" ", "-")
        if not slug:
            raise ValueError("project_slug must not be empty")
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if any(ch not in allowed for ch in slug):
            raise ValueError("project_slug must be [a-z0-9-_]")
        return slug

    @model_validator(mode="after")
    def validate_shot_uniqueness(self) -> "ShotList":
        seen: set[str] = set()
        duplicates: list[str] = []
        for shot in self.shots:
            if shot.id in seen:
                duplicates.append(shot.id)
            seen.add(shot.id)
        if duplicates:
            raise ValueError(f"duplicate shot ids: {', '.join(sorted(set(duplicates)))}")
        return self


class TakeAsset(BaseModel):
    path: str
    original_name: str
    sha256: str
    ext: str


class AssetEntry(BaseModel):
    shot_id: str
    takes: list[TakeAsset] = Field(default_factory=list)


class AssetsManifest(BaseModel):
    project_slug: str
    run_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    entries: list[AssetEntry] = Field(default_factory=list)
    missing_shots: list[str] = Field(default_factory=list)


class TakeScore(BaseModel):
    path: str
    duration_sec: float
    fps: float
    width: int
    height: int
    bit_rate: int
    brightness_mean: float
    freeze_ratio: float
    score: float
    score_detail: dict[str, float] = Field(default_factory=dict)
    conform_path: str | None = None


class MediaProbeEntry(BaseModel):
    shot_id: str
    takes: list[TakeScore] = Field(default_factory=list)
    selected_take: str
    render_src: str
    duration_sec: float
    notes: list[str] = Field(default_factory=list)


class MediaProbeManifest(BaseModel):
    project_slug: str
    run_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    entries: list[MediaProbeEntry] = Field(default_factory=list)


class NarrationEntry(BaseModel):
    shot_id: str
    text: str | None = None
    wav_path: str | None = None
    duration_sec: float = 0.0
    audio_query: dict[str, Any] | None = None


class NarrationManifest(BaseModel):
    project_slug: str
    run_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    entries: list[NarrationEntry] = Field(default_factory=list)


class TimelineShot(BaseModel):
    shot_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    duration_frames: int = Field(ge=1)
    duration_sec: float = Field(gt=0.0)
    video_src: str
    narration_src: str | None = None
    narration_duration_sec: float = 0.0
    subtitle_lines: list[str] = Field(default_factory=list)
    extend_mode: Literal["none", "freeze_last_subtle_zoom"] = "none"
    trim_policy: Literal["none", "from_start"] = "none"
    transition_to_next: TransitionSpec = Field(default_factory=TransitionSpec)


class Timeline(BaseModel):
    project_slug: str
    run_id: str
    fps: int = Field(ge=1)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    total_frames: int = Field(ge=1)
    total_duration_sec: float = Field(gt=0.0)
    shots: list[TimelineShot] = Field(default_factory=list, min_length=1)
    beat_sync_applied: bool = False


class RemotionProps(BaseModel):
    composition_id: str
    fps: int
    width: int
    height: int
    total_frames: int
    shots: list[dict[str, Any]]


class PromptVariant(BaseModel):
    id: str
    prompt: str
    focus: str


class StoryboardPrompt(BaseModel):
    shot_id: str
    scene_index: int = Field(ge=1)
    target_duration_sec: float = Field(gt=0.0)
    aspect_ratio: str
    prompt_main: str
    negative_prompt: str
    style_instructions: list[str] = Field(default_factory=list)
    quality_checks: list[str] = Field(default_factory=list)
    prompt_variants: list[PromptVariant] = Field(default_factory=list)


class SoraPromptPack(BaseModel):
    project_slug: str
    run_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    mode: Literal["storyboard"] = "storyboard"
    no_paid_api: bool = True
    scenes: list[StoryboardPrompt] = Field(default_factory=list)


class QualityFinding(BaseModel):
    shot_id: str | None = None
    severity: Literal["info", "warn", "error"] = "warn"
    code: str
    message: str
    recommendation: str | None = None


class DirectorQualityReport(BaseModel):
    project_slug: str
    run_id: str
    generated_at: str = Field(default_factory=utc_now_iso)
    findings: list[QualityFinding] = Field(default_factory=list)


class StepState(BaseModel):
    status: Literal["pending", "running", "success", "failed", "skipped"] = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None


class RunError(BaseModel):
    step_id: str
    error_type: str
    message: str
    recovery_hint: str | None = None
    at: str = Field(default_factory=utc_now_iso)


class RunState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    project_slug: str
    run_id: str
    started_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    steps: dict[str, StepState] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    qc_warnings: list[str] = Field(default_factory=list)
    errors: list[RunError] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_step_keys(self) -> "RunState":
        for step_id in STEP_IDS:
            self.steps.setdefault(step_id, StepState())
        return self
