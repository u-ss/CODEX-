# Video Finalize Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A9` として `draft.mp4` と `mix.wav` を統合し `final.mp4` を出力する。

## 手順

1. draft映像 + mix音声をmuxする
2. loudness正規化を適用（-14 LUFS, TP -1dB）
3. 失敗時はmuxのみのフォールバックを実施
4. 最終成果物を `exports/` へ保存

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/exports/final.mp4`

## Rules

- `+faststart` を付与して配信互換を担保
- フォールバック時は警告を記録
