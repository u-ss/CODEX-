# -*- coding: utf-8 -*-
"""
VOICEVOX APIクライアント — 接続管理・音声合成・辞書登録
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


@dataclass
class SpeakerStyle:
    """キャラクタースタイル情報"""
    name: str
    style_id: int
    style_name: str


class VoicevoxClient:
    """VOICEVOX APIクライアント"""

    def __init__(self, base_url: str = "http://localhost:50021", timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ─── 接続確認 ───
    def is_alive(self) -> bool:
        """VOICEVOX APIサーバーの生存確認"""
        try:
            resp = self._session.get(
                f"{self.base_url}/version", timeout=5
            )
            return resp.ok
        except requests.RequestException:
            return False

    def get_version(self) -> str:
        """バージョン取得"""
        resp = self._session.get(f"{self.base_url}/version", timeout=5)
        resp.raise_for_status()
        return resp.text.strip('"')

    # ─── キャラクター＆スタイル ───
    def get_speakers(self) -> List[Dict]:
        """キャラクター一覧を取得"""
        resp = self._session.get(f"{self.base_url}/speakers", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def find_style_id(self, char_name: str, style_name: str = "ノーマル") -> int:
        """キャラクター名+スタイル名からstyle_idを取得"""
        speakers = self.get_speakers()
        for speaker in speakers:
            if speaker["name"] == char_name:
                for style in speaker["styles"]:
                    if style["name"] == style_name:
                        return style["id"]
        raise ValueError(
            f"スタイル '{style_name}' がキャラクター '{char_name}' に見つかりません"
        )

    def list_styles(self) -> List[SpeakerStyle]:
        """全キャラクターの全スタイルを一覧化"""
        speakers = self.get_speakers()
        result = []
        for speaker in speakers:
            for style in speaker["styles"]:
                result.append(SpeakerStyle(
                    name=speaker["name"],
                    style_id=style["id"],
                    style_name=style["name"],
                ))
        return result

    # ─── 音声合成 ───
    def create_audio_query(self, text: str, speaker_id: int) -> Dict[str, Any]:
        """audio_query生成"""
        resp = self._session.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def synthesize(self, audio_query: Dict[str, Any], speaker_id: int) -> bytes:
        """audio_queryからWAVバイトを生成"""
        resp = self._session.post(
            f"{self.base_url}/synthesis",
            params={"speaker": speaker_id},
            json=audio_query,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.content

    def text_to_wav(
        self,
        text: str,
        speaker_id: int,
        output_path: Path,
        speed: float = 1.0,
        pitch: float = 0.0,
        intonation: float = 1.0,
        volume: float = 1.0,
        pre_phoneme: float = 0.1,
        post_phoneme: float = 0.1,
    ) -> Dict[str, Any]:
        """テキスト→WAVファイル生成（グローバルパラメータ適用）"""
        # audio_query生成
        query = self.create_audio_query(text, speaker_id)

        # グローバルパラメータ適用
        query["speedScale"] = speed
        query["pitchScale"] = pitch
        query["intonationScale"] = intonation
        query["volumeScale"] = volume
        query["prePhonemeLength"] = pre_phoneme
        query["postPhonemeLength"] = post_phoneme

        # WAV生成
        wav_data = self.synthesize(query, speaker_id)

        # ファイル保存
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(wav_data)

        return query

    # ─── モーラ単位制御 ───
    @staticmethod
    def adjust_mora_pitch(
        query: Dict, phrase_idx: int, mora_idx: int, pitch: float
    ) -> Dict:
        """特定のモーラのピッチを変更"""
        query["accent_phrases"][phrase_idx]["moras"][mora_idx]["pitch"] = pitch
        return query

    @staticmethod
    def boost_phrase_pitch(query: Dict, phrase_idx: int, boost: float = 0.3) -> Dict:
        """アクセント句全体のピッチをブースト（強調用）"""
        for mora in query["accent_phrases"][phrase_idx]["moras"]:
            if mora["pitch"] > 0:  # 無声化されていないモーラのみ
                mora["pitch"] += boost
        return query

    @staticmethod
    def apply_sentence_end_fall(query: Dict, fall_amount: float = 0.3) -> Dict:
        """文末下降パターンを適用"""
        phrases = query["accent_phrases"]
        if not phrases:
            return query
        last_phrase = phrases[-1]
        moras = last_phrase["moras"]
        n = len(moras)
        for i, mora in enumerate(moras):
            if mora["pitch"] > 0:
                # 後半のモーラほど下降量を大きく
                progress = i / max(n - 1, 1)
                mora["pitch"] -= fall_amount * progress
        return query

    @staticmethod
    def apply_question_rise(query: Dict, rise_amount: float = 0.5) -> Dict:
        """疑問文の語尾上昇パターンを適用"""
        phrases = query["accent_phrases"]
        if not phrases:
            return query
        last_phrase = phrases[-1]
        moras = last_phrase["moras"]
        if moras:
            # 最後の2-3モーラを上昇
            for mora in moras[-3:]:
                if mora["pitch"] > 0:
                    mora["pitch"] += rise_amount
        return query

    @staticmethod
    def insert_pause(
        query: Dict, after_phrase_idx: int, length: float = 0.15
    ) -> Dict:
        """アクセント句間にポーズを挿入"""
        query["accent_phrases"][after_phrase_idx]["pause_mora"] = {
            "text": "、",
            "consonant": None,
            "consonant_length": None,
            "vowel": "pau",
            "vowel_length": length,
            "pitch": 0.0,
        }
        return query

    # ─── アクセント句操作 ───
    def get_accent_phrases(
        self, text: str, speaker_id: int, *, is_kana: bool = False
    ) -> List[Dict[str, Any]]:
        """テキストからアクセント句を直接取得する"""
        resp = self._session.post(
            f"{self.base_url}/accent_phrases",
            params={
                "text": text,
                "speaker": speaker_id,
                "is_kana": str(is_kana).lower(),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def recalculate_mora_data(
        self,
        accent_phrases: List[Dict[str, Any]],
        speaker_id: int,
    ) -> List[Dict[str, Any]]:
        """accent修正済みのaccent_phrasesに対し、moraデータ（pitch/length）を再計算する"""
        resp = self._session.post(
            f"{self.base_url}/mora_data",
            params={"speaker": speaker_id},
            json=accent_phrases,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ─── ユーザー辞書 ───
    def register_word(
        self, surface: str, pronunciation: str, accent_type: int = 0
    ) -> str:
        """ユーザー辞書に単語を登録"""
        resp = self._session.post(
            f"{self.base_url}/user_dict_word",
            params={
                "surface": surface,
                "pronunciation": pronunciation,
                "accent_type": accent_type,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.text  # UUID返却

    # ─── リソース解放 ───
    def close(self):
        """セッションをクローズ"""
        self._session.close()
