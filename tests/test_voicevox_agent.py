# -*- coding: utf-8 -*-
"""
VOICEBOXエージェント テスト
VOICEVOX不要（モック使用）で状況分析・調整プラン・前処理のロジックを検証
"""
import sys
from pathlib import Path

import pytest

# パス追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from エージェント.VOICEVOXエージェント.scripts.situation_analyzer import (
    Emotion, Tension, SpeechStyle, analyze,
)
from エージェント.VOICEVOXエージェント.scripts.adjustment_planner import (
    create_plan, AdjustmentPlan,
)
from エージェント.VOICEVOXエージェント.scripts.presets import (
    PresetManager, VoicePreset, BUILTIN_PRESETS,
)
from エージェント.VOICEVOXエージェント.scripts.text_preprocessor import (
    preprocess, split_long_text, fix_elongation, insert_breath_points,
)


# ════════════════════════════════════════════
# 状況分析テスト
# ════════════════════════════════════════════

class TestSituationAnalyzer:
    """状況分析のテスト"""

    def test_anger_detection(self):
        """怒り感情のキーワード検出"""
        profile = analyze("ふざけるな！何だと！！")
        assert profile.emotion == Emotion.ANGER
        assert profile.tension == Tension.HIGH

    def test_joy_detection(self):
        """喜び感情のキーワード検出"""
        profile = analyze("やった！嬉しい！最高だ！")
        assert profile.emotion == Emotion.JOY

    def test_sadness_detection(self):
        """悲しみ感情のキーワード検出"""
        profile = analyze("残念だ…辛いな…")
        assert profile.emotion == Emotion.SADNESS

    def test_surprise_detection(self):
        """驚き感情の検出"""
        profile = analyze("えっ！まさか！信じられない！")
        assert profile.emotion == Emotion.SURPRISE

    def test_neutral_detection(self):
        """ニュートラルの検出"""
        profile = analyze("本日は晴れです。気温は20度。")
        assert profile.emotion == Emotion.NEUTRAL

    def test_question_style(self):
        """疑問文の検出"""
        profile = analyze("これは何ですか？")
        assert profile.style == SpeechStyle.QUESTION

    def test_command_style(self):
        """命令文の検出"""
        profile = analyze("逃げろ！急げ！")
        assert profile.style == SpeechStyle.COMMAND

    def test_monologue_style(self):
        """独白の検出"""
        profile = analyze("そうかもしれないな")
        assert profile.style == SpeechStyle.MONOLOGUE

    def test_statement_style(self):
        """平叙文の検出"""
        profile = analyze("今日は良い天気です。")
        assert profile.style == SpeechStyle.STATEMENT

    def test_high_tension(self):
        """高テンションの検出"""
        profile = analyze("すごい！！やった！！")
        assert profile.tension == Tension.HIGH

    def test_low_tension(self):
        """低テンションの検出"""
        # 3つの低テンション信号: 「しかし」キーワード + 「…」 + 長文＋読点2個以上
        text = "しかし、これは非常に複雑な問題であり、慎重に検討する必要がある\u2026"
        profile = analyze(text)
        assert profile.tension == Tension.LOW, f"got {profile.tension}, text_len={len(text)}"

    def test_emphasis_detection(self):
        """強調語の検出"""
        profile = analyze("「重要」なポイントはインフォメーションです")
        assert "重要" in profile.emphasis_words

    def test_confidence_increases_with_signals(self):
        """シグナルが多いほど確信度が上がる"""
        weak = analyze("残念。")
        strong = analyze("残念だ…辛い…悲しい…寂しい…")
        assert strong.confidence >= weak.confidence

    def test_to_dict(self):
        """辞書変換"""
        profile = analyze("テスト")
        d = profile.to_dict()
        assert "emotion" in d
        assert "tension" in d
        assert "style" in d
        assert "emphasis_words" in d


# ════════════════════════════════════════════
# プリセットテスト
# ════════════════════════════════════════════

class TestPresets:
    """プリセットのテスト"""

    def test_builtin_presets_exist(self):
        """6つの組込みプリセットが存在する"""
        pm = PresetManager()
        names = pm.list_names()
        assert "news" in names
        assert "youtube" in names
        assert "story" in names
        assert "game" in names
        assert "education" in names
        assert "conversation" in names

    def test_get_existing_preset(self):
        """存在するプリセットの取得"""
        pm = PresetManager()
        preset = pm.get("news")
        assert preset.name == "news"
        assert preset.speed == 1.0
        assert preset.intonation == 0.95

    def test_get_nonexistent_returns_conversation(self):
        """存在しないプリセット名はconversationを返す"""
        pm = PresetManager()
        preset = pm.get("nonexistent")
        assert preset.name == "conversation"

    def test_merge_with_adjustments(self):
        """プリセットに補正を適用"""
        pm = PresetManager()
        base = pm.get("news")
        merged = pm.merge_with_adjustments(base, {
            "speed_delta": 0.1,
            "intonation_delta": 0.2,
        })
        assert merged.speed == pytest.approx(base.speed + 0.1)
        assert merged.intonation == pytest.approx(base.intonation + 0.2)

    def test_add_custom_preset(self):
        """カスタムプリセストの追加"""
        pm = PresetManager()
        pm.add(VoicePreset(name="custom", speed=1.5, intonation=1.8))
        assert "custom" in pm.list_names()
        assert pm.get("custom").speed == 1.5


# ════════════════════════════════════════════
# テキスト前処理テスト
# ════════════════════════════════════════════

class TestTextPreprocessor:
    """テキスト前処理のテスト"""

    def test_split_long_text(self):
        """長文が分割される"""
        text = "これは最初の文。" * 10
        segments = split_long_text(text, max_chars=30)
        assert len(segments) > 1
        for seg in segments:
            assert len(seg) <= 50  # 句点含む分割なので多少超える

    def test_short_text_not_split(self):
        """短い文は分割されない"""
        text = "短い文です。"
        segments = split_long_text(text, max_chars=80)
        assert len(segments) == 1

    def test_fix_elongation(self):
        """伸ばし音補完"""
        assert fix_elongation("エネルギ不足") == "エネルギー不足"
        assert fix_elongation("カテゴリ分類") == "カテゴリー分類"
        # 既に「ー」がある場合は変更しない
        assert fix_elongation("エネルギー不足") == "エネルギー不足"

    def test_insert_breath_points(self):
        """呼吸ポイント挿入（長文のみ）"""
        short = "短い文"
        assert insert_breath_points(short) == short  # 変更なし

    def test_preprocess_pipeline(self):
        """前処理パイプライン全体"""
        text = "これはテストです。エネルギが重要です。"
        result = preprocess(text)
        assert len(result) >= 1
        # 伸ばし音が補完されているか
        combined = "".join(result)
        assert "エネルギー" in combined


# ════════════════════════════════════════════
# 調整プランテスト
# ════════════════════════════════════════════

class TestAdjustmentPlanner:
    """調整プラン策定のテスト"""

    def test_anger_increases_intonation(self):
        """怒り感情ではintonationが上がる"""
        profile = analyze("ふざけるな！何だと！！")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        conv = pm.get("conversation")
        assert plan.intonation > conv.intonation

    def test_sadness_decreases_speed(self):
        """悲しみ感情ではspeedが下がる"""
        profile = analyze("残念だ…辛いな…")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        conv = pm.get("conversation")
        assert plan.speed < conv.speed

    def test_neutral_uses_conversation(self):
        """ニュートラルではconversationベース"""
        profile = analyze("本日は晴れです。")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        # conversationかnewsが選ばれる
        assert plan.preset_name in ("conversation", "news")

    def test_explicit_preset_override(self):
        """明示的なプリセット指定が優先される"""
        profile = analyze("テスト")
        pm = PresetManager()
        plan = create_plan(profile, pm, base_preset="youtube")
        assert plan.preset_name == "youtube"

    def test_parameters_are_clamped(self):
        """パラメータが上下限に収まる"""
        # 極端な入力でもクランプされる
        profile = analyze("すごい！！やった！！最高！！嬉しい！！")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        assert 0.5 <= plan.speed <= 2.0
        assert -0.15 <= plan.pitch <= 0.15
        assert 0.5 <= plan.intonation <= 2.0
        assert 0.5 <= plan.volume <= 2.0

    def test_question_has_rise_adjustment(self):
        """疑問文にはrise調整がある"""
        profile = analyze("これは何ですか？")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        actions = [m.action for m in plan.mora_adjustments]
        assert "rise" in actions

    def test_statement_has_fall_adjustment(self):
        """平叙文にはfall調整がある"""
        profile = analyze("今日は良い天気です。")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        actions = [m.action for m in plan.mora_adjustments]
        assert "fall" in actions

    def test_style_name_for_anger(self):
        """怒り感情にスタイル名が設定される"""
        profile = analyze("ふざけるな！許さない！")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        assert plan.style_name is not None

    def test_plan_to_dict(self):
        """プランの辞書変換"""
        profile = analyze("テスト")
        pm = PresetManager()
        plan = create_plan(profile, pm)
        d = plan.to_dict()
        assert "preset_name" in d
        assert "speed" in d
        assert "intonation" in d
        assert "mora_adjustments" in d


# ═══════════════════════════════════════════════
# Layer 1: ベースチューニング テスト
# ═══════════════════════════════════════════════

from エージェント.VOICEVOXエージェント.scripts.base_tuner import (
    apply_downstep, apply_declination, apply_natural_variation,
    optimize_pauses, apply_sentence_end, apply_base_tuning,
)


def _make_query(num_phrases=3, moras_per_phrase=4, base_pitch=5.5):
    """テスト用のaudio_queryモックを生成"""
    phrases = []
    for pi in range(num_phrases):
        moras = []
        for mi in range(moras_per_phrase):
            moras.append({
                "text": "ア",
                "consonant": None,
                "consonant_length": None,
                "vowel": "a",
                "vowel_length": 0.15,
                "pitch": base_pitch,
            })
        phrases.append({
            "moras": moras,
            "accent": 2 if pi == 0 else 1,  # 全フレーズにアクセント核
            "pause_mora": None,
        })
    return {"accent_phrases": phrases, "speedScale": 1.0}


class TestBaseTuner:
    """Layer 1ベースチューニングテスト"""

    def test_downstep_lowers_subsequent_phrases(self):
        """ダウンステップ: 後続フレーズのpitchが低下する"""
        query = _make_query(3)
        original_p2 = query["accent_phrases"][1]["moras"][0]["pitch"]
        apply_downstep(query)
        new_p2 = query["accent_phrases"][1]["moras"][0]["pitch"]
        # 2番目のフレーズは1番目のアクセント核の後なのでpitch低下
        assert new_p2 < original_p2

    def test_downstep_preserves_unvoiced(self):
        """ダウンステップ: pitch=0（無声化モーラ）はスキップ"""
        query = _make_query(2)
        query["accent_phrases"][1]["moras"][0]["pitch"] = 0.0
        apply_downstep(query)
        assert query["accent_phrases"][1]["moras"][0]["pitch"] == 0.0

    def test_declination_gradual_fall(self):
        """デクリネーション: 末尾フレーズほどpitchが下がる"""
        query = _make_query(4)
        apply_declination(query)
        # 先頭フレーズは変化なし、末尾が最も低い
        first = query["accent_phrases"][0]["moras"][0]["pitch"]
        last = query["accent_phrases"][3]["moras"][0]["pitch"]
        assert first > last

    def test_natural_variation_adds_jitter(self):
        """ピッチ揺れ: 各モーラのpitchが微変動する"""
        query = _make_query(2, 4, base_pitch=5.0)
        apply_natural_variation(query)
        pitches = [
            m["pitch"]
            for p in query["accent_phrases"]
            for m in p["moras"]
        ]
        # 全部同じ値5.0ではなくなっている
        assert len(set(pitches)) > 1

    def test_optimize_pauses_shortens(self):
        """ポーズ最適化: 長すぎるポーズを短縮"""
        query = _make_query(2)
        query["accent_phrases"][0]["pause_mora"] = {
            "text": "、", "consonant": None, "consonant_length": None,
            "vowel": "pau", "vowel_length": 0.45, "pitch": 0.0,
        }
        optimize_pauses(query)
        new_len = query["accent_phrases"][0]["pause_mora"]["vowel_length"]
        assert new_len < 0.45  # 短縮された

    def test_sentence_end_fall_for_statement(self):
        """文末補正: 平叙文で最終モーラが下降"""
        query = _make_query(1, 4, base_pitch=5.5)
        orig_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        apply_sentence_end(query, is_question=False, is_command=False)
        new_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        assert new_last < orig_last

    def test_sentence_end_rise_for_question(self):
        """文末補正: 疑問文で最終モーラが上昇"""
        query = _make_query(1, 4, base_pitch=5.0)
        orig_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        apply_sentence_end(query, is_question=True)
        new_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        assert new_last > orig_last

    def test_sentence_end_command_fall(self):
        """文末補正: 命令文で最終モーラが急下降"""
        query = _make_query(1, 4, base_pitch=5.5)
        orig_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        apply_sentence_end(query, is_command=True)
        new_last = query["accent_phrases"][0]["moras"][-1]["pitch"]
        assert new_last < orig_last

    def test_apply_base_tuning_integration(self):
        """統合関数: 全処理が適用されてpitchが変動する"""
        query = _make_query(3, 5, base_pitch=5.5)
        # 全モーラの元pitch記録
        orig = [
            m["pitch"]
            for p in query["accent_phrases"]
            for m in p["moras"]
        ]
        apply_base_tuning(query)
        new = [
            m["pitch"]
            for p in query["accent_phrases"]
            for m in p["moras"]
        ]
        # 少なくとも一部のpitchは変化しているはず
        changed = sum(1 for o, n in zip(orig, new) if abs(o - n) > 0.001)
        assert changed > 0, "ベースチューニングでpitchが全く変化しなかった"


# ═══════════════════════════════════════════════
# Layer 0: アクセント辞書照合テスト
# ═══════════════════════════════════════════════

from エージェント.VOICEVOXエージェント.scripts.accent_verifier import (
    count_moras,
    get_accent_info,
    verify_accents,
    verify_and_fix_accents,
    _determine_accent_position,
    MorphemeInfo,
)


class TestAccentVerifier:
    """Layer 0 アクセント辞書照合テスト"""

    # ─── モーラ数計算 ───

    def test_count_moras_basic(self):
        """基本的なカタカナのモーラ数"""
        assert count_moras("キョウ") == 2  # キョ + ウ
        assert count_moras("テンキ") == 3  # テ + ン + キ
        assert count_moras("ア") == 1

    def test_count_moras_with_youon(self):
        """拗音を含むモーラ数"""
        assert count_moras("シャ") == 1  # シャ = 1モーラ
        assert count_moras("チュウ") == 2  # チュ + ウ
        assert count_moras("リョウ") == 2  # リョ + ウ

    def test_count_moras_with_special(self):
        """促音・撥音・長音符"""
        assert count_moras("ガッコウ") == 4  # ガ + ッ + コ + ウ
        assert count_moras("トーキョー") == 4  # ト + ー + キョ + ー
        assert count_moras("ニッポン") == 4  # ニ + ッ + ポ + ン

    # ─── MeCab形態素解析 ───

    def test_get_accent_info_returns_list(self):
        """get_accent_infoがリストを返す"""
        result = get_accent_info("今日は天気です")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_get_accent_info_has_fields(self):
        """各形態素に必要なフィールドがある"""
        result = get_accent_info("天気")
        if not result:  # MeCab未インストール時はスキップ
            pytest.skip("MeCab/fugashi が利用不可")
        morph = result[0]
        assert hasattr(morph, "surface")
        assert hasattr(morph, "reading")
        assert hasattr(morph, "pronunciation")
        assert hasattr(morph, "pos")
        assert hasattr(morph, "accent_type")
        assert hasattr(morph, "mora_count")

    def test_get_accent_info_content_word_has_accent(self):
        """自立語（名詞等）にはアクセント型が付く"""
        result = get_accent_info("天気")
        if not result:
            pytest.skip("MeCab/fugashi が利用不可")
        # 「天気」は名詞でaType=1
        tenki = [m for m in result if m.surface == "天気"]
        if tenki:
            assert tenki[0].accent_type >= 0, "名詞にアクセント型がない"

    # ─── アクセント位置決定 ───

    def test_determine_accent_heiban(self):
        """平板型(aType=0)のaccent位置"""
        morphemes = [MorphemeInfo(
            surface="さくら", reading="サクラ", pronunciation="サクラ",
            pos="名詞", accent_type=0, accent_con_type="*", mora_count=3,
        )]
        result = _determine_accent_position(morphemes, 3)
        assert result == 0  # 平板型

    def test_determine_accent_atamadaka(self):
        """頭高型(aType=1)のaccent位置"""
        morphemes = [MorphemeInfo(
            surface="箸", reading="ハシ", pronunciation="ハシ",
            pos="名詞", accent_type=1, accent_con_type="*", mora_count=2,
        )]
        result = _determine_accent_position(morphemes, 2)
        assert result == 1  # 頭高型：1モーラ目が高い

    def test_determine_accent_odaka(self):
        """尾高型のaccent位置"""
        morphemes = [MorphemeInfo(
            surface="橋", reading="ハシ", pronunciation="ハシ",
            pos="名詞", accent_type=2, accent_con_type="*", mora_count=2,
        )]
        result = _determine_accent_position(morphemes, 2)
        assert result == 2  # 尾高型：2モーラ目まで高い

    def test_determine_accent_no_content_word(self):
        """自立語がない場合はNoneを返す"""
        morphemes = [MorphemeInfo(
            surface="は", reading="ハ", pronunciation="ワ",
            pos="助詞", accent_type=-1, accent_con_type="*", mora_count=1,
        )]
        result = _determine_accent_position(morphemes, 1)
        assert result is None

    # ─── 照合テスト ───

    def test_verify_accents_returns_result(self):
        """verify_accentsがVerificationResultを返す"""
        query = _make_query(2, 3)
        result = verify_accents(query, "テスト文です")
        assert hasattr(result, "fixes")
        assert hasattr(result, "total_phrases")
        assert hasattr(result, "mismatches")

    def test_verify_accents_result_to_dict(self):
        """VerificationResult.to_dict()が辞書を返す"""
        query = _make_query(1)
        result = verify_accents(query, "テスト")
        d = result.to_dict()
        assert "total_phrases" in d
        assert "mismatches" in d
        assert "fixes" in d

    # ─── 修正テスト ───

    def test_verify_and_fix_no_client(self):
        """client=Noneでもクラッシュしない"""
        query = _make_query(2)
        # client=None, recalculate_pitch=False で修正のみ
        result = verify_and_fix_accents(
            query, "テスト文です", speaker_id=0, client=None,
            recalculate_pitch=False,
        )
        assert isinstance(result, dict)
        assert "accent_phrases" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
