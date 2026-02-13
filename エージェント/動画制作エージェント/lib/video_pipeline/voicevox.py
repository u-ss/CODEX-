from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .io import run_command


def synthesize_voicevox_wav(
    *,
    base_url: str,
    speaker: int,
    text: str,
    output_path: Path,
    speed_scale: float,
    pitch_scale: float,
    intonation_scale: float,
    volume_scale: float,
    pre_phoneme_length: float,
    post_phoneme_length: float,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    query_resp = requests.post(
        f"{base_url}/audio_query",
        params={"text": text, "speaker": speaker},
        timeout=timeout_sec,
    )
    query_resp.raise_for_status()
    audio_query = query_resp.json()
    audio_query["speedScale"] = speed_scale
    audio_query["pitchScale"] = pitch_scale
    audio_query["intonationScale"] = intonation_scale
    audio_query["volumeScale"] = volume_scale
    audio_query["prePhonemeLength"] = pre_phoneme_length
    audio_query["postPhonemeLength"] = post_phoneme_length

    synth_resp = requests.post(
        f"{base_url}/synthesis",
        params={"speaker": speaker},
        json=audio_query,
        timeout=timeout_sec,
    )
    synth_resp.raise_for_status()
    output_path.write_bytes(synth_resp.content)
    return audio_query


def check_voicevox_alive(base_url: str, timeout_sec: int = 5) -> bool:
    try:
        response = requests.get(f"{base_url}/version", timeout=timeout_sec)
        return response.ok
    except requests.RequestException:
        return False


def create_silence_wav(output_path: Path, duration_sec: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo",
        "-t",
        f"{duration_sec:.3f}",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"failed to generate silence wav: {result.stderr.strip()}")
