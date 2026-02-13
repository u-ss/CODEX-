# Video VoiceVox TTS Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A4` としてナレーションテキストをVOX音声へ変換し、manifest化する。

## 手順

1. `shot_list.normalized.json` からナレーション対象を抽出
2. VOICEVOX `audio_query` と `synthesis` を実行
3. `media/audio/<project>/narration/` にWAV保存
4. duration計測後 `narration_manifest.json` を出力

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/narration_manifest.json`

## Rules

- VOICEVOX接続不可時は失敗終了
- `audio_query` パラメータを再現可能な形で保存
