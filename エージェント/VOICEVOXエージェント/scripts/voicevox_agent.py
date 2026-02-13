# -*- coding: utf-8 -*-
"""
VOICEBOXエージェント CLIエントリポイント
状況分析 → 調整策定 → Antigravityレビュー → 音声生成
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# スクリプトディレクトリをパスに追加
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent.parent))

from エージェント.VOICEVOXエージェント.scripts.voicevox_client import VoicevoxClient
from エージェント.VOICEVOXエージェント.scripts.text_preprocessor import preprocess
from エージェント.VOICEVOXエージェント.scripts.situation_analyzer import analyze, SituationProfile
from エージェント.VOICEVOXエージェント.scripts.adjustment_planner import create_plan, AdjustmentPlan
from エージェント.VOICEVOXエージェント.scripts.presets import PresetManager
from エージェント.VOICEVOXエージェント.scripts.base_tuner import apply_base_tuning
from エージェント.VOICEVOXエージェント.scripts.accent_verifier import verify_and_fix_accents


def run_pipeline(
    text: str,
    speaker_id: int = 0,
    character: Optional[str] = None,
    scene: Optional[str] = None,
    preset_name: Optional[str] = None,
    base_url: str = "http://localhost:50021",
    output_dir: Optional[Path] = None,
    enable_base_tuning: bool = False,
    enable_accent_verification: bool = True,
) -> Dict:
    """
    VOICEBOXエージェントの全パイプラインを実行。

    フロー:
      1. テキスト前処理
      2. ルールベース状況分析
      3. 調整プラン策定（Layer 2）
      4. (Antigravityレビュー — 呼び出し元が判定)
      5a. Layer 0アクセント辞書照合（デフォルトON）
      5b. Layer 1ベースチューニング + Layer 2モーラ調整
      6. VOICEVOX音声合成

    Returns:
        実行ログ辞書
    """
    client = VoicevoxClient(base_url=base_url)

    # Step 0: 接続確認
    if not client.is_alive():
        return {"error": "VOICEVOX APIサーバーに接続できません。起動してください。"}

    # speaker_id解決
    if character:
        try:
            speaker_id = client.find_style_id(character, preset_name or "ノーマル")
        except ValueError as e:
            return {"error": str(e)}

    # Step 1: テキスト前処理
    segments = preprocess(text)

    # 出力ディレクトリ
    if output_dir is None:
        today = datetime.now().strftime("%Y%m%d")
        output_dir = Path(f"_outputs/voicebox/{today}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # プリセットマネージャ
    pm = PresetManager()

    results = []
    for i, segment in enumerate(segments):
        # Step 2: 状況分析
        profile = analyze(segment, context=scene or "")

        # Step 3: 調整プラン策定
        plan = create_plan(profile, pm, base_preset=preset_name)

        # Step 4: Antigravityレビュー（ここでは結果を返す — Antigravityが判断）
        review_data = format_review_request(segment, profile, plan)

        # Step 5: 音声生成
        timestamp = datetime.now().strftime("%H%M%S")
        wav_path = output_dir / f"segment_{i:03d}_{timestamp}.wav"

        # 5a: audio_query生成
        query = client.create_audio_query(segment, speaker_id)

        # 5a-1: Layer 0 アクセント辞書照合（デフォルトON）
        accent_log = None
        if enable_accent_verification:
            try:
                from エージェント.VOICEVOXエージェント.scripts.accent_verifier import verify_accents
                pre_result = verify_accents(query, segment)
                query = verify_and_fix_accents(
                    query, segment, speaker_id, client,
                    recalculate_pitch=True,
                )
                accent_log = pre_result.to_dict()
            except Exception as e:
                accent_log = {"error": str(e)}

        # 5a-2: Layer 2グローバルパラメータ適用
        query["speedScale"] = plan.speed
        query["pitchScale"] = plan.pitch
        query["intonationScale"] = plan.intonation
        query["volumeScale"] = plan.volume

        # 5b: Layer 1ベースチューニング（デフォルトOFF — 長文時に有効化）
        if enable_base_tuning:
            from エージェント.VOICEVOXエージェント.scripts.situation_analyzer import SpeechStyle
            apply_base_tuning(
                query,
                is_question=(profile.style == SpeechStyle.QUESTION),
                is_command=(profile.style == SpeechStyle.COMMAND),
            )

        # 5c: Layer 2モーラ単位調整（キーワード強調等）
        for adj in plan.mora_adjustments:
            phrase_count = len(query.get("accent_phrases", []))
            if phrase_count == 0:
                continue
            idx = adj.phrase_idx if adj.phrase_idx >= 0 else phrase_count - 1
            if idx >= phrase_count:
                continue

            if adj.action == "boost":
                query = VoicevoxClient.boost_phrase_pitch(query, idx, adj.amount)
            elif adj.action == "fall":
                query = VoicevoxClient.apply_sentence_end_fall(query, adj.amount)
            elif adj.action == "rise":
                query = VoicevoxClient.apply_question_rise(query, adj.amount)

        # 5d: 合成 + 保存
        wav_data = client.synthesize(query, speaker_id)
        output_path = wav_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(wav_data)

        # ログ作成
        segment_log = {
            "text": segment,
            "analysis": profile.to_dict(),
            "adjustment": plan.to_dict(),
            "accent_verification": accent_log,
            "review": review_data,
            "speaker_id": speaker_id,
            "output_path": str(wav_path),
        }
        results.append(segment_log)

    # 調整ログJSON保存
    log_path = output_dir / "adjustment_log.json"
    log_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    client.close()

    return {
        "status": "success",
        "segments": len(results),
        "output_dir": str(output_dir),
        "log_path": str(log_path),
        "results": results,
    }


def format_review_request(
    text: str,
    profile: SituationProfile,
    plan: AdjustmentPlan,
) -> Dict:
    """
    Antigravityレビュー用のデータをフォーマット。

    Antigravityはこの情報を見て:
      1. 感情判定が合っているか
      2. パラメータが極端でないか
      3. 強調語の選択が正しいか
    を判断する。
    """
    return {
        "review_prompt": (
            f"以下の音声調整プランをレビューしてください:\n"
            f"台詞: 「{text}」\n"
            f"感情判定: {profile.emotion.value} (確信度: {profile.confidence})\n"
            f"テンション: {profile.tension.value}\n"
            f"文体: {profile.style.value}\n"
            f"強調語: {profile.emphasis_words}\n"
            f"---\n"
            f"適用プリセット: {plan.preset_name}\n"
            f"話速: {plan.speed}, 音高: {plan.pitch}, "
            f"抑揚: {plan.intonation}, 音量: {plan.volume}\n"
            f"スタイル候補: {plan.style_name}\n"
            f"モーラ調整: {len(plan.mora_adjustments)}件\n"
            f"---\n"
            f"この調整は適切ですか？修正が必要な場合は理由を述べてください。"
        ),
        "analysis_summary": profile.to_dict(),
        "plan_summary": plan.to_dict(),
        "passed": True,  # デフォルトOK（Antigravityが変更する）
        "iterations": 0,
        "notes": "",
    }


def main():
    """CLIエントリポイント"""
    parser = argparse.ArgumentParser(
        description="VOICEBOX Agent v2.0.0 — 高品質音声生成エージェント"
    )
    parser.add_argument("--text", type=str, help="合成するテキスト")
    parser.add_argument("--file", type=str, help="テキストファイルのパス")
    parser.add_argument("--speaker", type=int, default=0, help="speaker_id (デフォルト: 0)")
    parser.add_argument("--character", type=str, help="キャラクター名")
    parser.add_argument("--scene", type=str, default="", help="シーン説明（コンテキスト）")
    parser.add_argument("--preset", type=str, help="プリセット名（省略時は自動選択）")
    parser.add_argument("--url", type=str, default="http://localhost:50021",
                        help="VOICEVOX APIのURL")
    parser.add_argument("--output", type=str, help="出力ディレクトリ")
    parser.add_argument("--check", action="store_true", help="VOICEVOX接続確認のみ")
    parser.add_argument("--list-speakers", action="store_true", help="キャラクター一覧表示")
    parser.add_argument("--analyze-only", action="store_true",
                        help="状況分析のみ（音声生成しない）")
    parser.add_argument("--base-tuning", action="store_true",
                        help="Layer 1ベースチューニングを有効化（長文向け）")

    args = parser.parse_args()

    # 接続確認モード
    if args.check:
        client = VoicevoxClient(base_url=args.url)
        if client.is_alive():
            version = client.get_version()
            print(f"✅ VOICEVOX接続OK (version: {version})")
        else:
            print("❌ VOICEVOX APIサーバーに接続できません")
            sys.exit(1)
        return

    # キャラクター一覧表示
    if args.list_speakers:
        client = VoicevoxClient(base_url=args.url)
        if not client.is_alive():
            print("❌ VOICEVOX接続不可")
            sys.exit(1)
        styles = client.list_styles()
        for s in styles:
            print(f"  [{s.style_id:3d}] {s.name} / {s.style_name}")
        return

    # テキスト取得
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        print("❌ --text または --file を指定してください")
        sys.exit(1)

    # 分析のみモード
    if args.analyze_only:
        segments = preprocess(text)
        pm = PresetManager()
        for i, seg in enumerate(segments):
            profile = analyze(seg, context=args.scene)
            plan = create_plan(profile, pm, base_preset=args.preset)
            print(f"\n--- セグメント {i+1} ---")
            print(f"テキスト: {seg}")
            print(f"感情: {profile.emotion.value} (確信度: {profile.confidence:.1f})")
            print(f"テンション: {profile.tension.value}")
            print(f"文体: {profile.style.value}")
            print(f"強調語: {profile.emphasis_words}")
            print(f"プリセット: {plan.preset_name}")
            print(f"パラメータ: speed={plan.speed}, pitch={plan.pitch}, "
                  f"intonation={plan.intonation}, volume={plan.volume}")
            print(f"スタイル: {plan.style_name}")
            print(f"根拠: {plan.reasoning}")
        return

    # フルパイプライン実行
    output_dir = Path(args.output) if args.output else None
    result = run_pipeline(
        text=text,
        speaker_id=args.speaker,
        character=args.character,
        scene=args.scene,
        preset_name=args.preset,
        base_url=args.url,
        output_dir=output_dir,
        enable_base_tuning=args.base_tuning,
    )

    if "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)

    print(f"✅ 生成完了: {result['segments']}セグメント")
    print(f"   出力先: {result['output_dir']}")
    print(f"   ログ: {result['log_path']}")


def _run_main_with_exit_code() -> int:
    try:
        main()
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 1 if code else 0
    return 0


if __name__ == "__main__":
    _here = Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in sys.path:
                sys.path.insert(0, str(_shared_dir))
            break
    try:
        from workflow_logging_hook import run_logged_main as _run_logged_main
    except Exception:
        raise SystemExit(_run_main_with_exit_code())
    else:
        raise SystemExit(
            _run_logged_main(
                "voicebox",
                "voicevox_agent",
                _run_main_with_exit_code,
                phase_name="VOICEVOX_AGENT_RUN",
            )
        )
