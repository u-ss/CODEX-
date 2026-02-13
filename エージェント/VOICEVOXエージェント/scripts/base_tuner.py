# -*- coding: utf-8 -*-
"""
ベースチューニング — Layer 1: 全出力に共通適用する汎用人間化処理

audio_queryのモーラpitchを直接操作して人間らしいイントネーションを実現する。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


def apply_base_tuning(
    query: Dict[str, Any],
    *,
    is_question: bool = False,
    is_command: bool = False,
) -> Dict[str, Any]:
    """
    Layer 1ベースチューニングを一括適用する統合関数。

    Args:
        query: VOICEVOX audio_query
        is_question: 疑問文かどうか
        is_command: 命令文かどうか

    Returns:
        補正済みのaudio_query
    """
    # ① ダウンステップ
    apply_downstep(query)
    # ② デクリネーション
    apply_declination(query)
    # ③ ナチュラルピッチバリエーション
    apply_natural_variation(query)
    # ④ ポーズ最適化
    optimize_pauses(query)
    # ⑤ 文末イントネーション補正
    apply_sentence_end(query, is_question=is_question, is_command=is_command)
    return query


def apply_downstep(
    query: Dict[str, Any],
    step_amount: float = 0.04,
    min_pitch: float = 4.5,
) -> Dict[str, Any]:
    """
    ダウンステップ: アクセント核を持つフレーズの後、
    後続フレーズ全体のpitchを段階的に低下させる。

    日本語韻律の最重要特徴。
    """
    phrases = query.get("accent_phrases", [])
    if len(phrases) < 2:
        return query

    # アクセント核（accent > 0）を持つフレーズを検出
    cumulative_drop = 0.0
    for i in range(1, len(phrases)):
        prev = phrases[i - 1]
        # 前のフレーズにアクセント核がある場合、ダウンステップ発動
        if prev.get("accent", 0) > 0:
            cumulative_drop += step_amount

        if cumulative_drop > 0:
            for mora in phrases[i].get("moras", []):
                if mora["pitch"] > 0:  # 無声化モーラはスキップ
                    new_p = mora["pitch"] - cumulative_drop
                    mora["pitch"] = max(min_pitch, new_p)

    return query


def apply_declination(
    query: Dict[str, Any],
    total_decline: float = 0.08,
) -> Dict[str, Any]:
    """
    デクリネーション: 発話全体を通してf0の基準線が緩やかに下降する。
    """
    phrases = query.get("accent_phrases", [])
    n = len(phrases)
    if n < 2:
        return query

    for i, phrase in enumerate(phrases):
        # 線形減衰: 先頭は0、末尾でtotal_decline
        decay = (i / (n - 1)) * total_decline
        for mora in phrase.get("moras", []):
            if mora["pitch"] > 0:
                mora["pitch"] = max(4.5, mora["pitch"] - decay)

    return query


def apply_natural_variation(
    query: Dict[str, Any],
    amplitude: float = 0.015,
) -> Dict[str, Any]:
    """
    ナチュラルピッチバリエーション: 各モーラに微小な正弦波的変動を加算。
    完全に平坦なピッチは機械的に聞こえるため、人間のジッタを模倣する。
    """
    phrases = query.get("accent_phrases", [])

    for pi, phrase in enumerate(phrases):
        for mi, mora in enumerate(phrase.get("moras", [])):
            if mora["pitch"] > 0:
                # 非周期的パターン（同じピッチ変化が繰り返されない）
                phase = pi * 7.3 + mi * 3.1
                variation = amplitude * math.sin(phase)
                mora["pitch"] += variation

    return query


def optimize_pauses(
    query: Dict[str, Any],
    comma_length: float = 0.12,
    period_length: float = 0.25,
) -> Dict[str, Any]:
    """
    ポーズ最適化: pause_moraのvowel_lengthを自然な値に補正。
    VOICEVOXのデフォルト（0.3-0.5秒）は長すぎるため短縮する。
    """
    phrases = query.get("accent_phrases", [])

    for phrase in phrases:
        pause = phrase.get("pause_mora")
        if pause and pause.get("vowel") == "pau":
            current = pause.get("vowel_length", 0.3)
            # 長いポーズ（0.3以上）は句点扱い、短いポーズは読点扱い
            if current >= 0.3:
                pause["vowel_length"] = period_length
            else:
                pause["vowel_length"] = comma_length

    return query


def apply_sentence_end(
    query: Dict[str, Any],
    *,
    is_question: bool = False,
    is_command: bool = False,
    fall_amount: float = 0.15,
    rise_amount: float = 0.25,
    command_fall: float = 0.20,
) -> Dict[str, Any]:
    """
    文末イントネーション補正:
    - 平叙文: 最終2-3モーラを下降
    - 疑問文: 最終2-3モーラを上昇
    - 命令文: 最終モーラを急下降
    """
    phrases = query.get("accent_phrases", [])
    if not phrases:
        return query

    last_phrase = phrases[-1]
    moras = last_phrase.get("moras", [])
    n = len(moras)
    if n == 0:
        return query

    if is_question:
        # 疑問文: 最後の3モーラを上昇
        for i, mora in enumerate(moras[-3:]):
            if mora["pitch"] > 0:
                progress = (i + 1) / min(3, n)
                mora["pitch"] += rise_amount * progress
    elif is_command:
        # 命令文: 最終モーラを急下降
        for mora in moras[-2:]:
            if mora["pitch"] > 0:
                mora["pitch"] = max(0.1, mora["pitch"] - command_fall)
    else:
        # 平叙文: 最終2-3モーラを緩やかに下降
        target_moras = moras[-3:] if n >= 3 else moras
        for i, mora in enumerate(target_moras):
            if mora["pitch"] > 0:
                progress = (i + 1) / len(target_moras)
                mora["pitch"] = max(0.1, mora["pitch"] - fall_amount * progress)

    return query
