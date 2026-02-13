from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_COMPOSITION_ID, VIDEO_EXTENSIONS
from .director import (
    augment_shot_list_payload,
    build_prompt_pack,
    build_quality_report,
    render_style_guide_markdown,
)
from .ffmpeg_utils import (
    beat_snap_frame,
    calculate_loudnorm_filter,
    ffmpeg_conform_video,
    file_sha256,
    frames_to_seconds,
    is_audio_effectively_empty,
    probe_wav_duration,
    sample_video_metrics,
    score_take,
    seconds_to_frames,
)
from .io import read_json, run_command, write_json
from .models import (
    AssetEntry,
    AssetsManifest,
    DirectorArtifacts,
    MediaProbeEntry,
    MediaProbeManifest,
    NarrationEntry,
    NarrationManifest,
    RemotionProps,
    ShotList,
    TakeAsset,
    TakeScore,
    Timeline,
    TimelineShot,
)
from .paths import PipelinePaths, normalize_path_str
from .schema import load_schema, validate_with_schema
from .subtitle import split_subtitle_lines
from .voicevox import check_voicevox_alive, synthesize_voicevox_wav


@dataclass
class StepOutput:
    artifacts: dict[str, str]
    warnings: list[str]


def resolve_bgm_path(raw_path: str | None, paths: PipelinePaths) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    project_relative = paths.project_dir / raw_path
    if project_relative.exists():
        return project_relative
    repo_relative = paths.repo_root / raw_path
    return repo_relative


def resolve_lut_path(raw_path: str | None, paths: PipelinePaths) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    project_relative = paths.project_dir / raw_path
    if project_relative.exists():
        return project_relative
    return paths.repo_root / raw_path


def load_shot_list(paths: PipelinePaths) -> ShotList:
    if paths.normalized_shot_list_path.is_file():
        return ShotList.model_validate(read_json(paths.normalized_shot_list_path))
    if paths.directed_shot_list_path.is_file():
        return ShotList.model_validate(read_json(paths.directed_shot_list_path))
    return ShotList.model_validate(read_json(paths.shot_list_path))


def build_director_artifacts(paths: PipelinePaths) -> DirectorArtifacts | None:
    directed = normalize_path_str(paths.directed_shot_list_path) if paths.directed_shot_list_path.is_file() else None
    prompt_pack = normalize_path_str(paths.sora_prompt_pack_path) if paths.sora_prompt_pack_path.is_file() else None
    style_guide = normalize_path_str(paths.sora_style_guide_path) if paths.sora_style_guide_path.is_file() else None
    quality_report = (
        normalize_path_str(paths.sora_quality_report_path) if paths.sora_quality_report_path.is_file() else None
    )
    if not any([directed, prompt_pack, style_guide, quality_report]):
        return None
    return DirectorArtifacts(
        run_id=paths.run_id,
        shot_list_directed=directed,
        sora_prompt_pack=prompt_pack,
        sora_style_guide=style_guide,
        sora_quality_report=quality_report,
    )


def load_assets_manifest(paths: PipelinePaths) -> AssetsManifest:
    return AssetsManifest.model_validate(read_json(paths.assets_manifest_path))


def load_media_probe(paths: PipelinePaths) -> MediaProbeManifest:
    return MediaProbeManifest.model_validate(read_json(paths.media_probe_path))


def load_narration_manifest(paths: PipelinePaths) -> NarrationManifest:
    return NarrationManifest.model_validate(read_json(paths.narration_manifest_path))


def load_timeline(paths: PipelinePaths) -> Timeline:
    return Timeline.model_validate(read_json(paths.timeline_path))


def step_d1_direct(paths: PipelinePaths, *, force: bool, dry_run: bool) -> StepOutput:
    if not paths.shot_list_path.is_file():
        raise FileNotFoundError(f"shot_list not found: {paths.shot_list_path}")
    raw = read_json(paths.shot_list_path)
    shot_list = ShotList.model_validate(raw)
    if not shot_list.settings.director.no_paid_api:
        raise ValueError("settings.director.no_paid_api must be true (paid API is disabled by policy)")
    prompt_pack = build_prompt_pack(shot_list, paths.run_id)
    quality_report = build_quality_report(shot_list, paths.run_id, prompt_pack)
    directed_payload = augment_shot_list_payload(raw, prompt_pack, quality_report, force=force)
    style_guide = render_style_guide_markdown(prompt_pack, quality_report)

    if not dry_run:
        write_json(paths.directed_shot_list_path, directed_payload)
        write_json(paths.sora_prompt_pack_path, prompt_pack.model_dump(mode="json"))
        write_json(paths.sora_quality_report_path, quality_report.model_dump(mode="json"))
        paths.sora_style_guide_path.write_text(style_guide, encoding="utf-8")

    warnings = [
        f"{finding.code}:{finding.message}"
        for finding in quality_report.findings
        if finding.severity in {"warn", "error"}
    ]
    if dry_run:
        warnings.append("dry_run_enabled_director_outputs_not_written")

    return StepOutput(
        artifacts={
            "shot_list.directed.json": normalize_path_str(paths.directed_shot_list_path),
            "sora_prompt_pack.json": normalize_path_str(paths.sora_prompt_pack_path),
            "sora_style_guide.md": normalize_path_str(paths.sora_style_guide_path),
            "sora_quality_report.json": normalize_path_str(paths.sora_quality_report_path),
        },
        warnings=warnings,
    )


def step_a1_validate(paths: PipelinePaths) -> StepOutput:
    schema_path = paths.schema_dir / "shot_list.schema.json"
    source_path = paths.directed_shot_list_path if paths.directed_shot_list_path.is_file() else paths.shot_list_path
    if not source_path.is_file():
        raise FileNotFoundError(f"shot_list not found: {source_path}")
    raw = read_json(source_path)
    validate_with_schema(raw, load_schema(schema_path))
    shot_list = ShotList.model_validate(raw)
    if shot_list.project_slug != paths.project_slug:
        raise ValueError("shot_list.project_slug does not match --project")
    director_artifacts = build_director_artifacts(paths)
    if director_artifacts:
        shot_list = shot_list.model_copy(update={"director_artifacts": director_artifacts})
    payload = shot_list.model_dump(mode="json")
    if payload.get("director_artifacts") is None:
        payload.pop("director_artifacts", None)
    write_json(paths.normalized_shot_list_path, payload)
    return StepOutput(
        artifacts={"shot_list.normalized.json": normalize_path_str(paths.normalized_shot_list_path)},
        warnings=[],
    )


def step_a2_collect(paths: PipelinePaths, *, force: bool, dry_run: bool) -> StepOutput:
    shot_list = load_shot_list(paths)
    archive_dir = paths.sora_inbox_dir / "archive" / paths.run_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    entries: list[AssetEntry] = []
    missing_shots: list[str] = []

    inbox_files = [p for p in paths.sora_inbox_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    for shot in shot_list.shots:
        matcher = (shot.video.inbox_match or shot.id).lower()
        matches = [item for item in inbox_files if matcher in item.name.lower()]
        dest_dir = paths.media_video_project_dir / shot.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        takes: list[TakeAsset] = []
        seen_sha: set[str] = set()
        take_index = 1
        for source in sorted(matches):
            digest = file_sha256(source)
            if digest in seen_sha:
                continue
            dest = dest_dir / f"{shot.id}_take{take_index:02d}{source.suffix.lower()}"
            if force or not dest.exists():
                if not dry_run:
                    shutil.copy2(source, dest)
            takes.append(
                TakeAsset(
                    path=normalize_path_str(dest),
                    original_name=source.name,
                    sha256=digest,
                    ext=source.suffix.lower(),
                )
            )
            seen_sha.add(digest)
            take_index += 1
            if not dry_run:
                archived = archive_dir / source.name
                if not archived.exists():
                    shutil.move(str(source), str(archived))
        if not takes:
            missing_shots.append(shot.id)
        entries.append(AssetEntry(shot_id=shot.id, takes=takes))

    manifest = AssetsManifest(
        project_slug=paths.project_slug,
        run_id=paths.run_id,
        entries=entries,
        missing_shots=missing_shots,
    )
    write_json(paths.assets_manifest_path, manifest.model_dump(mode="json"))
    if missing_shots:
        raise RuntimeError(f"missing assets for shots: {', '.join(missing_shots)}")
    return StepOutput(
        artifacts={"assets_manifest.json": normalize_path_str(paths.assets_manifest_path)},
        warnings=[],
    )


def step_a3_probe(paths: PipelinePaths, *, force: bool) -> StepOutput:
    shot_list = load_shot_list(paths)
    assets = load_assets_manifest(paths)
    settings = shot_list.settings
    lut_path = resolve_lut_path(settings.look.lut_path, paths)
    entries: list[MediaProbeEntry] = []

    shot_index = {shot.id: shot for shot in shot_list.shots}
    for entry in assets.entries:
        if not entry.takes:
            raise RuntimeError(f"no takes found for {entry.shot_id}")
        shot = shot_index[entry.shot_id]
        take_scores: list[TakeScore] = []
        for take in entry.takes:
            src = Path(take.path)
            metrics = sample_video_metrics(src)
            score, detail = score_take(
                width=metrics["width"],
                height=metrics["height"],
                duration_sec=metrics["duration_sec"],
                target_duration_sec=shot.timing.min_sec,
                bit_rate=metrics["bit_rate"],
                brightness_mean=metrics["brightness_mean"],
                freeze_ratio=metrics["freeze_ratio"],
            )
            take_scores.append(
                TakeScore(
                    path=normalize_path_str(src),
                    duration_sec=metrics["duration_sec"],
                    fps=metrics["fps"],
                    width=metrics["width"],
                    height=metrics["height"],
                    bit_rate=metrics["bit_rate"],
                    brightness_mean=metrics["brightness_mean"],
                    freeze_ratio=metrics["freeze_ratio"],
                    score=score,
                    score_detail=detail,
                )
            )

        take_scores.sort(key=lambda item: item.score, reverse=True)
        selected = take_scores[0]
        conform_path = paths.media_video_project_dir / shot.id / f"{shot.id}_conform.mp4"
        if force or not conform_path.exists():
            ffmpeg_conform_video(
                src=Path(selected.path),
                dst=conform_path,
                fps=settings.fps,
                width=settings.resolution.width,
                height=settings.resolution.height,
                lut_path=lut_path if lut_path and lut_path.exists() else None,
                contrast=settings.look.contrast,
                saturation=settings.look.saturation,
                brightness=settings.look.brightness,
                gamma=settings.look.gamma,
            )
        conform_metrics = sample_video_metrics(conform_path)
        selected.conform_path = normalize_path_str(conform_path)
        notes: list[str] = []
        if selected.fps < settings.fps - 0.01:
            notes.append("source_fps_lower_than_target")
        if selected.width < settings.resolution.width or selected.height < settings.resolution.height:
            notes.append("source_resolution_lower_than_target")

        entries.append(
            MediaProbeEntry(
                shot_id=entry.shot_id,
                takes=take_scores,
                selected_take=selected.path,
                render_src=normalize_path_str(conform_path),
                duration_sec=conform_metrics["duration_sec"],
                notes=notes,
            )
        )

    manifest = MediaProbeManifest(
        project_slug=paths.project_slug,
        run_id=paths.run_id,
        entries=entries,
    )
    write_json(paths.media_probe_path, manifest.model_dump(mode="json"))
    return StepOutput(
        artifacts={"media_probe.json": normalize_path_str(paths.media_probe_path)},
        warnings=[],
    )


def step_a4_tts(paths: PipelinePaths, *, force: bool) -> StepOutput:
    shot_list = load_shot_list(paths)
    voice = shot_list.settings.voicevox
    if not check_voicevox_alive(voice.base_url):
        raise RuntimeError(
            f"VOICEVOX is not available at {voice.base_url}. start engine and retry."
        )

    entries: list[NarrationEntry] = []
    for shot in shot_list.shots:
        text = (shot.narration or "").strip()
        if not text:
            entries.append(NarrationEntry(shot_id=shot.id, text=None, wav_path=None, duration_sec=0.0))
            continue
        wav_path = paths.narration_dir / f"{shot.id}.wav"
        audio_query = None
        if force or not wav_path.exists():
            audio_query = synthesize_voicevox_wav(
                base_url=voice.base_url,
                speaker=voice.speaker,
                text=text,
                output_path=wav_path,
                speed_scale=voice.speedScale,
                pitch_scale=voice.pitchScale,
                intonation_scale=voice.intonationScale,
                volume_scale=voice.volumeScale,
                pre_phoneme_length=voice.prePhonemeLength,
                post_phoneme_length=voice.postPhonemeLength,
            )
        duration = probe_wav_duration(wav_path)
        entries.append(
            NarrationEntry(
                shot_id=shot.id,
                text=text,
                wav_path=normalize_path_str(wav_path),
                duration_sec=duration,
                audio_query=audio_query,
            )
        )

    manifest = NarrationManifest(project_slug=paths.project_slug, run_id=paths.run_id, entries=entries)
    write_json(paths.narration_manifest_path, manifest.model_dump(mode="json"))
    return StepOutput(
        artifacts={"narration_manifest.json": normalize_path_str(paths.narration_manifest_path)},
        warnings=[],
    )


def step_a5_timing(paths: PipelinePaths) -> StepOutput:
    shot_list = load_shot_list(paths)
    probe = load_media_probe(paths)
    narration = load_narration_manifest(paths)
    fps = shot_list.settings.fps
    width = shot_list.settings.resolution.width
    height = shot_list.settings.resolution.height
    max_chars = shot_list.settings.subtitle_max_chars

    probe_map = {item.shot_id: item for item in probe.entries}
    narration_map = {item.shot_id: item for item in narration.entries}
    durations: list[int] = []
    min_frames: list[int] = []
    max_frames: list[int] = []
    shot_rows: list[dict[str, object]] = []
    for shot in shot_list.shots:
        probed = probe_map.get(shot.id)
        if not probed:
            raise RuntimeError(f"media probe entry missing for {shot.id}")
        narr = narration_map.get(shot.id) or NarrationEntry(shot_id=shot.id)
        base_sec = max(narr.duration_sec + shot.timing.post_pad_sec, shot.timing.min_sec)
        if base_sec > shot.timing.max_sec:
            raise RuntimeError(f"narration too long for {shot.id}: {base_sec:.3f}s > max_sec")
        dur_frames = seconds_to_frames(base_sec, fps)
        shot_min = seconds_to_frames(shot.timing.min_sec, fps)
        shot_max = seconds_to_frames(shot.timing.max_sec, fps)
        dur_frames = max(shot_min, min(dur_frames, shot_max))
        extend_mode = "freeze_last_subtle_zoom" if probed.duration_sec + 1e-9 < base_sec else "none"
        trim_policy = "from_start" if probed.duration_sec > base_sec + 1e-9 else "none"
        subtitle_text = shot.subtitle or shot.narration or ""
        subtitle_lines = split_subtitle_lines(subtitle_text, max_chars=max_chars, max_lines=2)

        durations.append(dur_frames)
        min_frames.append(shot_min)
        max_frames.append(shot_max)
        shot_rows.append(
            {
                "shot_id": shot.id,
                "video_src": probed.render_src,
                "narration_src": narr.wav_path,
                "narration_duration_sec": narr.duration_sec,
                "subtitle_lines": subtitle_lines,
                "extend_mode": extend_mode,
                "trim_policy": trim_policy,
                "transition_to_next": shot.transition_to_next,
            }
        )

    beat_applied = False
    cumulative = 0
    for idx in range(len(durations) - 1):
        cumulative += durations[idx]
        target = beat_snap_frame(
            frame=cumulative,
            fps=fps,
            bpm=shot_list.settings.bgm.bpm,
            offset_sec=shot_list.settings.bgm.offset_sec,
            max_snap_frames=shot_list.settings.beat_snap_max_frames,
        )
        delta = target - cumulative
        if delta == 0:
            continue
        candidate = durations[idx] + delta
        if min_frames[idx] <= candidate <= max_frames[idx]:
            durations[idx] = candidate
            cumulative = target
            beat_applied = True

    timeline_shots: list[TimelineShot] = []
    frame_cursor = 0
    for idx, row in enumerate(shot_rows):
        duration_frames = durations[idx]
        start = frame_cursor
        end = start + duration_frames
        frame_cursor = end
        timeline_shots.append(
            TimelineShot(
                shot_id=str(row["shot_id"]),
                start_frame=start,
                end_frame=end,
                duration_frames=duration_frames,
                duration_sec=frames_to_seconds(duration_frames, fps),
                video_src=str(row["video_src"]),
                narration_src=row["narration_src"] if isinstance(row["narration_src"], str) else None,
                narration_duration_sec=float(row["narration_duration_sec"]),
                subtitle_lines=list(row["subtitle_lines"]),
                extend_mode=str(row["extend_mode"]),
                trim_policy=str(row["trim_policy"]),
                transition_to_next=row["transition_to_next"],
            )
        )

    timeline = Timeline(
        project_slug=paths.project_slug,
        run_id=paths.run_id,
        fps=fps,
        width=width,
        height=height,
        total_frames=frame_cursor,
        total_duration_sec=frames_to_seconds(frame_cursor, fps),
        shots=timeline_shots,
        beat_sync_applied=beat_applied,
    )
    write_json(paths.timeline_path, timeline.model_dump(mode="json"))
    return StepOutput(
        artifacts={"timeline.json": normalize_path_str(paths.timeline_path)},
        warnings=[],
    )


def step_a6_props(paths: PipelinePaths) -> StepOutput:
    timeline = load_timeline(paths)
    shots: list[dict[str, object]] = []
    for shot in timeline.shots:
        shots.append(
            {
                "shot_id": shot.shot_id,
                "start_frame": shot.start_frame,
                "duration_frames": shot.duration_frames,
                "video_src": normalize_path_str(Path(shot.video_src)),
                "subtitle_lines": shot.subtitle_lines,
                "transition_to_next": shot.transition_to_next.model_dump(mode="json"),
                "extend_mode": shot.extend_mode,
                "trim_policy": shot.trim_policy,
            }
        )
    props = RemotionProps(
        composition_id=DEFAULT_COMPOSITION_ID,
        fps=timeline.fps,
        width=timeline.width,
        height=timeline.height,
        total_frames=timeline.total_frames,
        shots=shots,
    )
    write_json(paths.remotion_props_path, props.model_dump(mode="json"))
    return StepOutput(
        artifacts={"remotion_props.json": normalize_path_str(paths.remotion_props_path)},
        warnings=[],
    )


def step_a7_render(paths: PipelinePaths) -> StepOutput:
    entry = paths.remotion_dir / "src" / "index.ts"
    if not entry.is_file():
        raise FileNotFoundError(f"remotion entry not found: {entry}")
    if not paths.remotion_props_path.is_file():
        raise FileNotFoundError(f"remotion props not found: {paths.remotion_props_path}")

    command = [
        "npx",
        "remotion",
        "render",
        str(entry),
        DEFAULT_COMPOSITION_ID,
        str(paths.draft_path),
        "--props-file",
        str(paths.remotion_props_path),
        "--overwrite",
    ]
    result = run_command(command, cwd=paths.remotion_dir, timeout_sec=3600)
    if result.returncode != 0 and "--props-file" in (result.stderr or ""):
        props_json = paths.remotion_props_path.read_text(encoding="utf-8")
        fallback = [
            "npx",
            "remotion",
            "render",
            str(entry),
            DEFAULT_COMPOSITION_ID,
            str(paths.draft_path),
            "--props",
            props_json,
            "--overwrite",
        ]
        result = run_command(fallback, cwd=paths.remotion_dir, timeout_sec=3600)
    if result.returncode != 0:
        raise RuntimeError(f"remotion render failed: {result.stderr.strip()}")
    return StepOutput(
        artifacts={"draft.mp4": normalize_path_str(paths.draft_path)},
        warnings=[],
    )


def step_a8_mix(paths: PipelinePaths) -> StepOutput:
    shot_list = load_shot_list(paths)
    timeline = load_timeline(paths)
    total_sec = timeline.total_duration_sec

    input_args: list[str] = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    filter_parts: list[str] = [f"[0:a]atrim=0:{total_sec:.3f},asetpts=N/SR/TB[nbase]"]
    narr_labels: list[str] = ["[nbase]"]
    next_input_index = 1
    for shot in timeline.shots:
        if not shot.narration_src:
            continue
        narration_path = Path(shot.narration_src)
        if not narration_path.is_file():
            continue
        input_args.extend(["-i", str(narration_path)])
        delay_ms = int(round((shot.start_frame / timeline.fps) * 1000.0))
        label = f"n{next_input_index}"
        filter_parts.append(
            f"[{next_input_index}:a]adelay={delay_ms}|{delay_ms},atrim=0:{total_sec:.3f},asetpts=N/SR/TB[{label}]"
        )
        narr_labels.append(f"[{label}]")
        next_input_index += 1

    narr_mix_label = "narr"
    if len(narr_labels) == 1:
        filter_parts.append(f"{narr_labels[0]}anull[{narr_mix_label}]")
    else:
        filter_parts.append("".join(narr_labels) + f"amix=inputs={len(narr_labels)}:normalize=0[{narr_mix_label}]")

    bgm_path = resolve_bgm_path(shot_list.settings.bgm.path, paths)
    final_label = "mix"
    if bgm_path and bgm_path.is_file():
        input_args.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        bgm_idx = next_input_index
        fade_out_start = max(0.0, total_sec - shot_list.settings.bgm.fade_out_sec)
        filter_parts.append(
            f"[{bgm_idx}:a]atrim=0:{total_sec:.3f},volume={shot_list.settings.bgm.volume_db:.2f}dB,"
            f"afade=t=in:st=0:d={shot_list.settings.bgm.fade_in_sec:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={shot_list.settings.bgm.fade_out_sec:.3f}[bgm]"
        )
        if shot_list.settings.bgm.ducking_enabled:
            filter_parts.append(
                f"[bgm][{narr_mix_label}]sidechaincompress=threshold={shot_list.settings.bgm.duck_threshold}:"
                f"ratio={shot_list.settings.bgm.duck_ratio}:attack=20:release=250[bgduck]"
            )
            filter_parts.append(f"[bgduck][{narr_mix_label}]amix=inputs=2:normalize=0[{final_label}]")
        else:
            filter_parts.append(f"[bgm][{narr_mix_label}]amix=inputs=2:normalize=0[{final_label}]")
    else:
        filter_parts.append(f"[{narr_mix_label}]anull[{final_label}]")

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        *input_args,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        f"[{final_label}]",
        "-t",
        f"{total_sec:.3f}",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(paths.mix_path),
    ]
    result = run_command(command, timeout_sec=1200)
    if result.returncode != 0:
        raise RuntimeError(f"audio mix failed: {result.stderr.strip()}")
    warnings: list[str] = []
    if is_audio_effectively_empty(paths.mix_path):
        warnings.append("mixed_audio_is_near_silence")
    return StepOutput(
        artifacts={"mix.wav": normalize_path_str(paths.mix_path)},
        warnings=warnings,
    )


def step_a9_finalize(paths: PipelinePaths) -> StepOutput:
    shot_list = load_shot_list(paths)
    if not paths.draft_path.is_file():
        raise FileNotFoundError(f"draft video not found: {paths.draft_path}")
    if not paths.mix_path.is_file():
        raise FileNotFoundError(f"mix wav not found: {paths.mix_path}")

    loudnorm = calculate_loudnorm_filter(
        target_lufs=shot_list.settings.export.target_lufs,
        truepeak_db=shot_list.settings.export.truepeak_db,
    )
    warnings: list[str] = []
    primary = run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(paths.draft_path),
        "-i",
        str(paths.mix_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-af",
        loudnorm,
        "-movflags",
        "+faststart",
        str(paths.final_path),
    ], timeout_sec=1800)
    if primary.returncode != 0:
        fallback = run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(paths.draft_path),
            "-i",
            str(paths.mix_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-movflags",
            "+faststart",
            str(paths.final_path),
        ], timeout_sec=1800)
        if fallback.returncode != 0:
            raise RuntimeError(f"finalize failed: {fallback.stderr.strip()}")
        warnings.append("loudnorm_failed_fallback_mux_applied")
    return StepOutput(
        artifacts={"exports/final.mp4": normalize_path_str(paths.final_path)},
        warnings=warnings,
    )
