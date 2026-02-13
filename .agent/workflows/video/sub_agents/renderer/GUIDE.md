# Video Remotion Renderer Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A7` として `remotion_props.json` を用いて draft動画を生成する。

## 手順

1. Remotion project を読み込む
2. propsを指定してrenderを実行
3. draft動画を出力する

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/draft.mp4`

## Rules

- 出力は映像主体（音声はA8/A9で最終化）
- レンダラー失敗時はstderrを保存
