from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .io import run_command


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_fraction(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        if den == "0":
            return 0.0
        return float(num) / float(den)
    return float(value)


def ffprobe_json(path: Path) -> dict[str, Any]:
    result = run_command([
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def find_video_stream(payload: dict[str, Any]) -> dict[str, Any]:
    streams = payload.get("streams", [])
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    raise RuntimeError("no video stream found")


def duration_from_probe(payload: dict[str, Any]) -> float:
    fmt = payload.get("format", {})
    duration = fmt.get("duration")
    if duration is not None:
        return float(duration)
    video = find_video_stream(payload)
    if video.get("duration") is not None:
        return float(video["duration"])
    return 0.0


def probe_wav_duration(path: Path) -> float:
    payload = ffprobe_json(path)
    return duration_from_probe(payload)


def estimate_brightness(path: Path) -> float:
    result = run_command([
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-vf",
        "signalstats",
        "-f",
        "null",
        "-",
    ], timeout_sec=300)
    if result.returncode != 0:
        return 128.0
    values = [float(item) for item in re.findall(r"YAVG:([0-9]+(?:\.[0-9]+)?)", result.stderr)]
    if not values:
        return 128.0
    return sum(values) / len(values)


def estimate_freeze_ratio(path: Path, duration_sec: float) -> float:
    if duration_sec <= 0.0:
        return 1.0
    result = run_command([
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-vf",
        "freezedetect=n=-50dB:d=0.5",
        "-f",
        "null",
        "-",
    ], timeout_sec=300)
    if result.returncode != 0:
        return 0.0
    durations = [float(item) for item in re.findall(r"freeze_duration: ([0-9]+(?:\.[0-9]+)?)", result.stderr)]
    total = sum(durations)
    ratio = total / duration_sec
    return max(0.0, min(ratio, 1.0))


def escape_lut_path_for_filter(path: Path) -> str:
    raw = str(path.resolve()).replace("\\", "/")
    raw = raw.replace(":", "\\:")
    return raw


def ffmpeg_conform_video(
    *,
    src: Path,
    dst: Path,
    fps: int,
    width: int,
    height: int,
    lut_path: Path | None,
    contrast: float,
    saturation: float,
    brightness: float,
    gamma: float,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    look_filters: list[str] = []
    if lut_path:
        look_filters.append(f"lut3d=file='{escape_lut_path_for_filter(lut_path)}'")
    else:
        look_filters.append(
            f"eq=contrast={contrast:.4f}:saturation={saturation:.4f}:brightness={brightness:.4f}:gamma={gamma:.4f}"
        )
    look_filters.extend([
        f"scale={width}:{height}:flags=lanczos",
        "setsar=1",
        f"fps={fps}",
    ])
    result = run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(src),
        "-vf",
        ",".join(look_filters),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "medium",
        "-crf",
        "18",
        str(dst),
    ], timeout_sec=1800)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conform failed: {result.stderr.strip()}")


def sample_video_metrics(path: Path) -> dict[str, float]:
    payload = ffprobe_json(path)
    video = find_video_stream(payload)
    duration_sec = duration_from_probe(payload)
    fps = parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    bit_rate = int(video.get("bit_rate") or payload.get("format", {}).get("bit_rate") or 0)
    brightness_mean = estimate_brightness(path)
    freeze_ratio = estimate_freeze_ratio(path, duration_sec)
    return {
        "duration_sec": duration_sec,
        "fps": fps,
        "width": width,
        "height": height,
        "bit_rate": bit_rate,
        "brightness_mean": brightness_mean,
        "freeze_ratio": freeze_ratio,
    }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def score_take(
    *,
    width: int,
    height: int,
    duration_sec: float,
    target_duration_sec: float,
    bit_rate: int,
    brightness_mean: float,
    freeze_ratio: float,
) -> tuple[float, dict[str, float]]:
    resolution_score = clamp(min(width / 1920.0, 1.0) * min(height / 1080.0, 1.0), 0.0, 1.0)
    target = max(target_duration_sec, 0.1)
    duration_score = clamp(1.0 - abs(duration_sec - target) / target, 0.0, 1.0)
    bitrate_score = clamp(bit_rate / 8_000_000.0, 0.0, 1.0)
    brightness_score = clamp(1.0 - abs(brightness_mean - 128.0) / 128.0, 0.0, 1.0)
    motion_score = clamp(1.0 - freeze_ratio, 0.0, 1.0)
    weighted = (
        resolution_score * 0.30
        + duration_score * 0.25
        + brightness_score * 0.15
        + bitrate_score * 0.15
        + motion_score * 0.15
    )
    return weighted, {
        "resolution": resolution_score,
        "duration_fit": duration_score,
        "brightness": brightness_score,
        "bitrate": bitrate_score,
        "motion": motion_score,
    }


def beat_snap_frame(
    *,
    frame: int,
    fps: int,
    bpm: float,
    offset_sec: float,
    max_snap_frames: int,
) -> int:
    if bpm <= 0:
        return frame
    interval = fps * (60.0 / bpm)
    if interval <= 0:
        return frame
    offset = offset_sec * fps
    index = round((frame - offset) / interval)
    target = int(round(offset + index * interval))
    if abs(target - frame) > max_snap_frames:
        return frame
    return target


def seconds_to_frames(seconds: float, fps: int) -> int:
    return max(1, int(round(seconds * fps)))


def frames_to_seconds(frames: int, fps: int) -> float:
    return frames / float(fps)


def calculate_loudnorm_filter(target_lufs: float, truepeak_db: float) -> str:
    return f"loudnorm=I={target_lufs}:TP={truepeak_db}:LRA=11"


def is_audio_effectively_empty(path: Path) -> bool:
    result = run_command([
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ])
    if result.returncode != 0:
        return False
    match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", result.stderr)
    if not match:
        return False
    value = float(match.group(1))
    return math.isinf(value) or value < -70.0
