# -*- coding: utf-8 -*-
"""
参照音声F0転写 — Layer 3B: 参照音声のpitchカーブをVOICEVOXモーラに転写する

参照WAVからF0(基本周波数)を抽出し、
VOICEVOXのaudio_queryのモーラピッチにマッピングする。

使い方:
    from f0_transfer import extract_f0_curve, apply_f0_to_query
    
    f0_curve = extract_f0_curve("reference.wav")
    query = apply_f0_to_query(query, f0_curve)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# F0抽出
# ═══════════════════════════════════════════════


def extract_f0_curve(
    audio_path: str | Path,
    *,
    sr: int = 24000,
    fmin: float = 50.0,
    fmax: float = 600.0,
    hop_length: int = 256,
) -> Optional[np.ndarray]:
    """
    音声ファイルからF0(基本周波数)カーブを抽出する。

    Args:
        audio_path: WAVファイルパス
        sr: サンプリングレート（VOICEVOX=24000Hz）
        fmin: F0推定の下限Hz
        fmax: F0推定の上限Hz
        hop_length: フレームシフト（サンプル数）

    Returns:
        F0カーブ(Hz) np.ndarray。無声部分は0.0。
        失敗時はNone。
    """
    try:
        import librosa
    except ImportError:
        logger.warning("librosa未インストール。pip install librosa")
        return None

    audio_path = Path(audio_path)
    if not audio_path.exists():
        logger.warning(f"音声ファイルが見つかりません: {audio_path}")
        return None

    try:
        # 音声読み込み
        y, _sr = librosa.load(str(audio_path), sr=sr, mono=True)

        # pyin でF0推定（librosa推奨の方式）
        f0, voiced_flag, voiced_prob = librosa.pyin(
            y,
            sr=sr,
            fmin=fmin,
            fmax=fmax,
            hop_length=hop_length,
        )

        # NaNを0に変換（無声部分）
        f0 = np.nan_to_num(f0, nan=0.0)

        logger.info(
            f"F0抽出完了: {audio_path.name}, "
            f"{len(f0)}フレーム, "
            f"有声率={np.count_nonzero(f0)/len(f0)*100:.1f}%, "
            f"F0範囲={f0[f0>0].min():.1f}~{f0[f0>0].max():.1f}Hz"
        )
        return f0

    except Exception as e:
        logger.warning(f"F0抽出失敗: {e}")
        return None


# ═══════════════════════════════════════════════
# F0 → VOICEVOXピッチ変換
# ═══════════════════════════════════════════════


def _hz_to_voicevox_pitch(f0_hz: float, base_hz: float = 300.0) -> float:
    """
    F0(Hz)をVOICEVOXのpitchスケールに変換する。

    VOICEVOXのpitchは対数スケールで概ね5.0〜6.0の範囲。
    基準周波数(base_hz)を5.5として対数変換する。

    Args:
        f0_hz: 基本周波数(Hz)
        base_hz: 基準周波数（pitch=5.5に対応）
    """
    if f0_hz <= 0:
        return 0.0  # 無声

    # 対数変換: 12半音=2倍の周波数
    semitones = 12.0 * math.log2(f0_hz / base_hz)
    # VOICEVOX pitchスケール: 5.5を基準、1半音≒0.1
    pitch = 5.5 + semitones * 0.1
    return max(3.0, min(8.0, pitch))  # クランプ


def _estimate_mora_durations(
    accent_phrases: List[Dict[str, Any]],
) -> List[Tuple[int, int, float]]:
    """
    アクセント句のモーラから、各モーラの推定時間位置を計算する。

    Returns:
        [(phrase_idx, mora_idx, duration_sec), ...]
    """
    result = []
    for pi, phrase in enumerate(accent_phrases):
        moras = phrase.get("moras", [])
        for mi, mora in enumerate(moras):
            # モーラの長さ = 子音 + 母音
            consonant_len = mora.get("consonant_length", 0.0) or 0.0
            vowel_len = mora.get("vowel_length", 0.0) or 0.0
            duration = consonant_len + vowel_len
            result.append((pi, mi, duration))

        # ポーズモーラ
        pause = phrase.get("pause_mora")
        if pause:
            pause_len = pause.get("vowel_length", 0.0) or 0.0
            result.append((pi, -1, pause_len))  # -1 = ポーズ

    return result


# ═══════════════════════════════════════════════
# F0カーブのモーラへのマッピング
# ═══════════════════════════════════════════════


def apply_f0_to_query(
    query: Dict[str, Any],
    f0_curve: np.ndarray,
    *,
    sr: int = 24000,
    hop_length: int = 256,
    base_hz: float = 0.0,
    blend: float = 0.7,
) -> Dict[str, Any]:
    """
    F0カーブをaudio_queryのモーラピッチに適用する。

    Args:
        query: VOICEVOX audio_query
        f0_curve: F0カーブ(Hz)
        sr: サンプリングレート
        hop_length: pyin抽出時のホップ幅
        base_hz: 基準周波数（0=自動推定）
        blend: ブレンド率 (0.0=元のまま, 1.0=完全転写)

    Returns:
        F0が適用されたaudio_query
    """
    accent_phrases = query.get("accent_phrases", [])
    mora_durations = _estimate_mora_durations(accent_phrases)

    if not mora_durations:
        return query

    # F0カーブの時間解像度
    frame_duration = hop_length / sr  # 1フレームの秒数

    # 基準周波数の自動推定（有声部分の中央値）
    voiced_f0 = f0_curve[f0_curve > 0]
    if len(voiced_f0) == 0:
        logger.warning("有声F0が見つかりません。F0転写スキップ。")
        return query

    if base_hz <= 0:
        base_hz = float(np.median(voiced_f0))
        logger.info(f"基準周波数を自動推定: {base_hz:.1f}Hz")

    # 各モーラの時間位置を計算し、F0カーブからpitchを取得
    current_time = 0.0
    for pi, mi, duration in mora_durations:
        if mi == -1:
            # ポーズはスキップ
            current_time += duration
            continue

        # モーラの中心時間 → F0フレーム
        center_time = current_time + duration / 2.0
        frame_idx = int(center_time / frame_duration)

        if frame_idx >= len(f0_curve):
            current_time += duration
            continue

        # 周辺フレームの平均F0（3フレーム幅）
        start = max(0, frame_idx - 1)
        end = min(len(f0_curve), frame_idx + 2)
        f0_slice = f0_curve[start:end]
        voiced_slice = f0_slice[f0_slice > 0]

        if len(voiced_slice) > 0:
            avg_f0 = float(np.mean(voiced_slice))
            new_pitch = _hz_to_voicevox_pitch(avg_f0, base_hz)

            # 元のpitchとブレンド
            phrase = accent_phrases[pi]
            mora = phrase["moras"][mi]
            old_pitch = mora.get("pitch", 0.0)

            if old_pitch > 0 and new_pitch > 0:
                mora["pitch"] = old_pitch * (1.0 - blend) + new_pitch * blend

        current_time += duration

    return query


# ═══════════════════════════════════════════════
# 便利関数
# ═══════════════════════════════════════════════


def transfer_f0(
    query: Dict[str, Any],
    reference_audio: str | Path,
    *,
    blend: float = 0.7,
) -> Dict[str, Any]:
    """
    参照音声のF0をaudio_queryに転写するワンライナー。

    Args:
        query: VOICEVOX audio_query
        reference_audio: 参照WAVファイルパス
        blend: ブレンド率 (0.0〜1.0)

    Returns:
        F0が適用されたaudio_query
    """
    f0_curve = extract_f0_curve(reference_audio)
    if f0_curve is None:
        return query

    return apply_f0_to_query(query, f0_curve, blend=blend)
