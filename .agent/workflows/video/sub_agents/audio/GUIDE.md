# Video Audio Mixer Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A8` としてナレーションとBGMをmixし `mix.wav` を生成する。

## 手順

1. `timeline.json` からナレーション配置時刻を取得
2. ナレーションwavを時刻配置して合成
3. BGMがある場合は `sidechaincompress` でダッキング
4. `mix.wav` を出力

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/mix.wav`

## Rules

- BGM未指定時はナレーションのみで完了
- クリップを避けるため適切なgainを適用
