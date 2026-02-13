# -*- coding: utf-8 -*-
"""
アクセント辞書照合 — Layer 0: 正しいアクセント位置を設定する

MeCab + UniDic で正確なアクセント型を取得し、
VOICEVOXのaudio_queryと照合・補正する。

使い方:
    query = client.create_audio_query(text, speaker_id)
    query = verify_and_fix_accents(query, text, speaker_id, client)
    # → Layer 1, Layer 2 へ
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# MeCab/fugashi 初期化（遅延ロード）
# ═══════════════════════════════════════════════

_tagger = None


def _get_tagger():
    """fugashi + unidic-lite のタガーを遅延初期化して返す"""
    global _tagger
    if _tagger is not None:
        return _tagger

    try:
        import fugashi
        import unidic_lite

        dicdir = unidic_lite.DICDIR
        dicrc = os.path.join(dicdir, "dicrc")
        # Windowsパスにスペースが含まれる場合の対策
        _tagger = fugashi.GenericTagger(f'-d "{dicdir}" -r "{dicrc}"')
        logger.info("fugashi + unidic-lite 初期化成功")
    except ImportError:
        logger.warning(
            "fugashi/unidic-lite がインストールされていません。"
            "pip install fugashi unidic-lite でインストールしてください。"
        )
        _tagger = None
    except Exception as e:
        logger.warning(f"MeCab初期化失敗: {e}")
        _tagger = None

    return _tagger


# ═══════════════════════════════════════════════
# データ構造
# ═══════════════════════════════════════════════


@dataclass
class MorphemeInfo:
    """形態素情報（MeCab解析結果）"""
    surface: str          # 表層形（漢字等）
    reading: str          # 読み（カタカナ）
    pronunciation: str    # 発音形（カタカナ）
    pos: str              # 品詞大分類
    accent_type: int      # アクセント型（-1=不明）
    accent_con_type: str  # アクセント結合型
    mora_count: int       # モーラ数


@dataclass
class AccentFix:
    """アクセント修正結果"""
    phrase_idx: int       # 修正対象のアクセント句インデックス
    old_accent: int       # 修正前のaccent値
    new_accent: int       # 修正後のaccent値
    word: str             # 対応する単語
    confidence: float     # 修正の確信度 (0.0-1.0)
    reason: str           # 修正理由


@dataclass
class VerificationResult:
    """照合結果"""
    fixes: List[AccentFix] = field(default_factory=list)
    morphemes: List[MorphemeInfo] = field(default_factory=list)
    total_phrases: int = 0
    mismatches: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_phrases": self.total_phrases,
            "mismatches": self.mismatches,
            "fixes": [
                {
                    "phrase_idx": f.phrase_idx,
                    "old_accent": f.old_accent,
                    "new_accent": f.new_accent,
                    "word": f.word,
                    "confidence": f.confidence,
                    "reason": f.reason,
                }
                for f in self.fixes
            ],
        }


# ═══════════════════════════════════════════════
# カタカナ→モーラ数の計算
# ═══════════════════════════════════════════════

# 拗音（2文字で1モーラ）
_YOUON = set("ャュョァィゥェォ")
# 促音・撥音（各1モーラ）
_SPECIAL = set("ッン")
# 長音符（1モーラ）
_CHOON = "ー"


def count_moras(kana: str) -> int:
    """カタカナ文字列のモーラ数を計算する"""
    count = 0
    i = 0
    while i < len(kana):
        ch = kana[i]
        if ch == _CHOON:
            count += 1
        elif ch in _YOUON:
            # 先頭に来る場合は1モーラ扱い（通常は前の文字と合わせて1モーラ）
            pass  # 前の文字でカウント済み
        elif ch in _SPECIAL:
            count += 1
        else:
            count += 1
            # 次が拗音なら合わせて1モーラ
            if i + 1 < len(kana) and kana[i + 1] in _YOUON:
                i += 1  # 拗音をスキップ
        i += 1
    return count


# ═══════════════════════════════════════════════
# MeCab形態素解析
# ═══════════════════════════════════════════════


def get_accent_info(text: str) -> List[MorphemeInfo]:
    """
    テキストをMeCab+UniDicで解析し、各形態素のアクセント情報を返す。

    Returns:
        各形態素のMorphemeInfoリスト
    """
    tagger = _get_tagger()
    if tagger is None:
        return []

    words = tagger(text)
    result = []

    for w in words:
        feat = str(w.feature)
        fields = [f.strip().strip("'()") for f in feat.split(",")]

        # UniDic-liteフィールド配置:
        #   [0] 品詞大分類
        #   [6] 語彙素読み
        #   [9] 発音形出現形
        #   [23] アクセント型（数値 or *）
        #   [24] アクセント結合型
        pos = fields[0] if len(fields) > 0 else "*"
        reading = fields[6] if len(fields) > 6 else w.surface
        pronunciation = fields[9] if len(fields) > 9 else reading
        accent_con_type = fields[24] if len(fields) > 24 else "*"

        # アクセント型の解析
        accent_type = -1
        if len(fields) > 23:
            raw = fields[23].strip()
            if raw.isdigit():
                accent_type = int(raw)
            elif raw == "0":
                accent_type = 0

        # モーラ数計算
        mora_count = count_moras(pronunciation) if pronunciation != "*" else 0

        result.append(MorphemeInfo(
            surface=w.surface,
            reading=reading,
            pronunciation=pronunciation,
            pos=pos,
            accent_type=accent_type,
            accent_con_type=accent_con_type,
            mora_count=mora_count,
        ))

    return result


# ═══════════════════════════════════════════════
# 照合と修正
# ═══════════════════════════════════════════════


def _map_morphemes_to_phrases(
    morphemes: List[MorphemeInfo],
    accent_phrases: List[Dict[str, Any]],
) -> List[Tuple[int, List[MorphemeInfo]]]:
    """
    MeCab形態素をVOICEVOXアクセント句にマッピングする。

    VOICEVOXのアクセント句は複数のMeCab形態素をまとめて1句にすることが多い。
    マッピングはモーラのテキストを照合して行う。

    Returns:
        [(phrase_idx, [対応するMorphemeInfo, ...]), ...]
    """
    result = []

    # 各アクセント句のテキストを結合
    phrase_texts = []
    for phrase in accent_phrases:
        moras = phrase.get("moras", [])
        text = "".join(m.get("text", "") for m in moras)
        phrase_texts.append(text)

    # 形態素の読みをカタカナで結合し、アクセント句と照合
    morph_idx = 0
    for pi, phrase_text in enumerate(phrase_texts):
        matched = []
        remaining = phrase_text

        while remaining and morph_idx < len(morphemes):
            morph = morphemes[morph_idx]
            # 形態素の読みがアクセント句テキストの先頭にマッチするか
            pron = morph.pronunciation
            if pron == "*" or not pron:
                morph_idx += 1
                continue

            if remaining.startswith(pron):
                matched.append(morph)
                remaining = remaining[len(pron):]
                morph_idx += 1
            elif len(pron) > 0 and remaining[:1] == pron[:1]:
                # 部分マッチ（読みのバリエーションで微妙にずれる場合）
                matched.append(morph)
                remaining = remaining[len(pron):]
                morph_idx += 1
            else:
                # マッチしない場合、次の形態素を試す
                morph_idx += 1

        result.append((pi, matched))

    return result


def _determine_accent_position(
    morphemes: List[MorphemeInfo],
    phrase_mora_count: int,
) -> Optional[int]:
    """
    形態素のaType情報から、アクセント句全体のaccent位置を決定する。

    アクセント型(aType)のルール:
        0 = 平板型（下がり目なし）→ accent = 0 は VOICEVOXだとモーラ数
        1 = 頭高型（1モーラ目の後で下降）→ accent = 1
        N = N番目のモーラまで高い → accent = N

    Returns:
        推定accent位置。判定不能なら None。
    """
    if not morphemes:
        return None

    # 自立語（名詞、動詞、形容詞、副詞）のアクセント型を優先
    content_words = [
        m for m in morphemes
        if m.pos in ("名詞", "動詞", "形容詞", "副詞", "連体詞", "接続詞", "感動詞")
        and m.accent_type >= 0
    ]

    if not content_words:
        return None

    # 最初の自立語のアクセント型を基準にする
    primary = content_words[0]

    if primary.accent_type == 0:
        # 平板型: VOICEVOXでは accent = モーラ数（最後まで高い）
        # ただし、正確には0を返す（VOICEVOXの0は平板型を意味する）
        return 0

    # 頭高・中高・尾高型: primary.accent_type = N → 最初のN番目で下がる
    # ただし、句内での位置を考慮する必要がある
    # 最初の自立語の前に何モーラあるか計算
    prefix_moras = 0
    for m in morphemes:
        if m is primary:
            break
        prefix_moras += m.mora_count

    accent_pos = prefix_moras + primary.accent_type
    # 句全体のモーラ数を超えないようクランプ
    if accent_pos > phrase_mora_count:
        accent_pos = phrase_mora_count

    return accent_pos


def verify_accents(
    query: Dict[str, Any],
    text: str,
) -> VerificationResult:
    """
    audio_queryのアクセント位置をMeCab辞書と照合する。

    修正は行わず、不一致の検出のみ。

    Args:
        query: VOICEVOX audio_query
        text: 元テキスト

    Returns:
        VerificationResult（不一致箇所のリスト含む）
    """
    morphemes = get_accent_info(text)
    if not morphemes:
        logger.warning("形態素解析結果が空。照合スキップ。")
        return VerificationResult()

    accent_phrases = query.get("accent_phrases", [])
    result = VerificationResult(
        morphemes=morphemes,
        total_phrases=len(accent_phrases),
    )

    # マッピング
    mapping = _map_morphemes_to_phrases(morphemes, accent_phrases)

    for phrase_idx, matched_morphemes in mapping:
        if phrase_idx >= len(accent_phrases):
            continue

        phrase = accent_phrases[phrase_idx]
        current_accent = phrase.get("accent", 0)
        phrase_moras = phrase.get("moras", [])
        phrase_mora_count = len(phrase_moras)

        # 辞書から推定されるaccent位置
        estimated = _determine_accent_position(matched_morphemes, phrase_mora_count)

        if estimated is None:
            continue  # 判定不能はスキップ

        # 不一致チェック
        if estimated != current_accent:
            word = "".join(m.surface for m in matched_morphemes)
            primary = next(
                (m for m in matched_morphemes if m.accent_type >= 0),
                None,
            )
            confidence = 0.8 if primary and primary.pos in ("名詞", "動詞") else 0.6

            result.fixes.append(AccentFix(
                phrase_idx=phrase_idx,
                old_accent=current_accent,
                new_accent=estimated,
                word=word,
                confidence=confidence,
                reason=(
                    f"辞書: aType={primary.accent_type if primary else '?'}, "
                    f"推定accent={estimated}, 現在accent={current_accent}"
                ),
            ))
            result.mismatches += 1

    return result


def verify_and_fix_accents(
    query: Dict[str, Any],
    text: str,
    speaker_id: int,
    client: Any,
    *,
    min_confidence: float = 0.5,
    recalculate_pitch: bool = True,
) -> Dict[str, Any]:
    """
    audio_queryのアクセント位置を照合し、不一致を修正する。

    Args:
        query: VOICEVOX audio_query
        text: 元テキスト
        speaker_id: 話者ID
        client: VoicevoxClientインスタンス
        min_confidence: この確信度以上の修正のみ適用
        recalculate_pitch: True=修正後にpitchを再計算

    Returns:
        修正済みのaudio_query
    """
    result = verify_accents(query, text)

    if not result.fixes:
        logger.info("アクセント不一致なし。修正不要。")
        return query

    logger.info(f"アクセント不一致: {result.mismatches}件検出")

    # 修正適用
    accent_phrases = query.get("accent_phrases", [])
    applied = 0
    modified_indices = set()  # 修正した句のインデックス

    for fix in result.fixes:
        if fix.confidence < min_confidence:
            logger.debug(
                f"確信度不足でスキップ: {fix.word} "
                f"(confidence={fix.confidence:.2f} < {min_confidence})"
            )
            continue

        if fix.phrase_idx < len(accent_phrases):
            accent_phrases[fix.phrase_idx]["accent"] = fix.new_accent
            modified_indices.add(fix.phrase_idx)
            applied += 1
            logger.info(
                f"修正: [{fix.phrase_idx}] {fix.word} "
                f"accent {fix.old_accent} → {fix.new_accent} "
                f"({fix.reason})"
            )

    if applied == 0:
        return query

    # accent修正した句のみpitchを再計算（修正してない句はVOICEVOX元来のpitchを維持）
    if recalculate_pitch:
        try:
            query = _recalculate_mora_pitch(
                query, accent_phrases, speaker_id, client,
                modified_indices=modified_indices,
            )
            logger.info(f"pitch再計算完了（{applied}件のaccent修正のみ反映）")
        except Exception as e:
            logger.warning(f"pitch再計算失敗（accent修正は維持）: {e}")

    return query


def _recalculate_mora_pitch(
    query: Dict[str, Any],
    accent_phrases: List[Dict[str, Any]],
    speaker_id: int,
    client: Any,
    *,
    modified_indices: Optional[set] = None,
) -> Dict[str, Any]:
    """
    accent修正後のmoraピッチをアクセント規則に基づいて自前計算する。
    修正した句（modified_indices）のみ再計算し、他はVOICEVOX元来のpitchを保持。

    日本語のアクセント規則:
        accent=0 (平板型): 低高高高... → 1モーラ目が低く残り全部高い
        accent=1 (頭高型): 高低低低... → 1モーラ目だけ高い
        accent=N (中高/尾高型): 低高...高低...低 → Nモーラ目まで高い
    """
    for pi, phrase in enumerate(accent_phrases):
        # 修正した句のみ再計算
        if modified_indices is not None and pi not in modified_indices:
            continue

        moras = phrase.get("moras", [])
        accent = phrase.get("accent", 0)
        n = len(moras)

        if n == 0:
            continue

        # 有声モーラ（pitch > 0）のみ対象
        voiced_indices = [i for i, m in enumerate(moras) if m.get("pitch", 0) > 0]
        if not voiced_indices:
            continue

        # 既存pitchの統計（有声のみ）
        voiced_pitches = [moras[i]["pitch"] for i in voiced_indices]
        p_mean = sum(voiced_pitches) / len(voiced_pitches)
        p_max = max(voiced_pitches)
        p_min = min(voiced_pitches)

        # 高低差: 既存の高低差を維持（最低0.2、最大0.5）
        spread = max(0.2, min(p_max - p_min, 0.5))
        p_high = p_mean + spread * 0.5
        p_low = p_mean - spread * 0.5

        # アクセント規則に基づく高低パターン生成
        # True=高い, False=低い
        pattern = _accent_pattern(accent, n)

        # パターンに基づいてpitch適用
        for i, mora in enumerate(moras):
            if mora.get("pitch", 0) == 0:
                continue  # 無声化モーラは維持

            if pattern[i]:
                # 高い位置: 句内位置に応じた微細な下降（自然さ）
                pos_ratio = i / max(n - 1, 1)
                mora["pitch"] = p_high - (spread * 0.1 * pos_ratio)
            else:
                # 低い位置
                mora["pitch"] = p_low

        # アクセント核直後の急下降を自然に（低へのスムーズな遷移）
        if accent > 0 and accent < n:
            # accent位置のモーラ（最後の高いモーラ）
            hi_idx = accent - 1
            lo_idx = accent
            if (hi_idx < n and lo_idx < n
                    and moras[hi_idx].get("pitch", 0) > 0
                    and moras[lo_idx].get("pitch", 0) > 0):
                # 急下降をやや滑らかに
                moras[lo_idx]["pitch"] = p_low + spread * 0.1

    query["accent_phrases"] = accent_phrases
    return query


def _accent_pattern(accent: int, mora_count: int) -> List[bool]:
    """
    アクセント型とモーラ数から高低パターンを生成する。

    Returns:
        各モーラの高低 (True=高い, False=低い) のリスト
    """
    if mora_count == 0:
        return []

    if mora_count == 1:
        # 1モーラ語: 常に高い
        return [True]

    if accent == 0:
        # 平板型: 低高高高...（1モーラ目だけ低い）
        return [False] + [True] * (mora_count - 1)

    if accent == 1:
        # 頭高型: 高低低低...（1モーラ目だけ高い）
        return [True] + [False] * (mora_count - 1)

    # 中高型/尾高型: 低高...高低...低
    # accent=N → Nモーラ目まで高い（ただし1モーラ目は低い）
    pattern = [False] * mora_count
    for i in range(1, min(accent, mora_count)):
        pattern[i] = True
    return pattern

