# Video Media Probe Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A3` として素材の特性測定・take採点・Conformを実行する。

## 手順

1. ffprobeでFPS/解像度/duration/bitrateを取得
2. take採点を行い `selected_take` を決定
3. Conform (`1920x1080`, `24fps`, `SAR=1`, `yuv420p`) を生成
4. LUTまたは簡易グレードを適用
5. `media_probe.json` を出力

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/media_probe.json`

## Rules

- 壊れた素材を検知したら失敗終了
- Remotion入力はConform済み素材のみ使用
