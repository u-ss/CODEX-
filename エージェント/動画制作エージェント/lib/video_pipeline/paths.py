from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PipelinePaths:
    repo_root: Path
    project_slug: str
    run_id: str

    @property
    def project_dir(self) -> Path:
        return self.repo_root / "projects" / self.project_slug

    @property
    def shot_list_path(self) -> Path:
        return self.project_dir / "shot_list.json"

    @property
    def output_dir(self) -> Path:
        return self.repo_root / "_outputs" / "video_pipeline" / self.project_slug / self.run_id

    @property
    def log_path(self) -> Path:
        return self.repo_root / "_logs" / "video_pipeline" / self.project_slug / f"{self.run_id}.jsonl"

    @property
    def run_state_path(self) -> Path:
        return self.output_dir / "run_state.json"

    @property
    def normalized_shot_list_path(self) -> Path:
        return self.output_dir / "shot_list.normalized.json"

    @property
    def directed_shot_list_path(self) -> Path:
        return self.output_dir / "shot_list.directed.json"

    @property
    def assets_manifest_path(self) -> Path:
        return self.output_dir / "assets_manifest.json"

    @property
    def media_probe_path(self) -> Path:
        return self.output_dir / "media_probe.json"

    @property
    def narration_manifest_path(self) -> Path:
        return self.output_dir / "narration_manifest.json"

    @property
    def timeline_path(self) -> Path:
        return self.output_dir / "timeline.json"

    @property
    def remotion_props_path(self) -> Path:
        return self.output_dir / "remotion_props.json"

    @property
    def sora_prompt_pack_path(self) -> Path:
        return self.output_dir / "sora_prompt_pack.json"

    @property
    def sora_style_guide_path(self) -> Path:
        return self.output_dir / "sora_style_guide.md"

    @property
    def sora_quality_report_path(self) -> Path:
        return self.output_dir / "sora_quality_report.json"

    @property
    def draft_path(self) -> Path:
        return self.output_dir / "draft.mp4"

    @property
    def mix_path(self) -> Path:
        return self.output_dir / "mix.wav"

    @property
    def exports_dir(self) -> Path:
        return self.output_dir / "exports"

    @property
    def final_path(self) -> Path:
        return self.exports_dir / "final.mp4"

    @property
    def sora_inbox_dir(self) -> Path:
        return self.repo_root / "sora_inbox"

    @property
    def media_dir(self) -> Path:
        return self.repo_root / "media"

    @property
    def media_video_project_dir(self) -> Path:
        return self.media_dir / "video" / self.project_slug

    @property
    def media_audio_project_dir(self) -> Path:
        return self.media_dir / "audio" / self.project_slug

    @property
    def narration_dir(self) -> Path:
        return self.media_audio_project_dir / "narration"

    @property
    def bgm_dir(self) -> Path:
        return self.media_audio_project_dir / "bgm"

    @property
    def remotion_dir(self) -> Path:
        return self.repo_root / "エージェント" / "動画制作エージェント" / "remotion"

    @property
    def schema_dir(self) -> Path:
        return self.repo_root / "エージェント" / "動画制作エージェント" / "schemas"

    def ensure_runtime_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.sora_inbox_dir.mkdir(parents=True, exist_ok=True)
        self.media_video_project_dir.mkdir(parents=True, exist_ok=True)
        self.narration_dir.mkdir(parents=True, exist_ok=True)
        self.bgm_dir.mkdir(parents=True, exist_ok=True)


def normalize_path_str(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")
