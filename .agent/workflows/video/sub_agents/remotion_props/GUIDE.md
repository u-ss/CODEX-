# Video Remotion Props Builder Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A6` として `timeline.json` をRemotion入力向けに変換する。

## 手順

1. `timeline.json` を読み込む
2. パスを絶対化し、Windows区切り差を吸収する
3. Remotion構成用propsを出力する

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/remotion_props.json`

## Rules

- 変換ロジックは可逆性を意識する
- パス解決の失敗は即エラー
