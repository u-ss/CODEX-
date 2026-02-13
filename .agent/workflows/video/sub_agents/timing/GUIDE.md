# Video Timing Builder Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A5` としてショット時刻・字幕・音声配置を算出し `timeline.json` を生成する。

## 手順

1. `media_probe.json` と `narration_manifest.json` を読み込む
2. ルールに従ってshot尺を算出
3. BGMビートへカット点を吸着（±8フレーム以内）
4. 字幕を最適分割
5. `timeline.json` を出力

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/timeline.json`

## Rules

- `max_sec` 超過時はエラー
- 尺不足時はextendルールを明示する
