# Video Asset Collector Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A2` として inbox素材をショットIDと照合し、正規名で `media/` に配置する。

## 手順

1. `sora_inbox/` を走査し `*<shot_id>*` を照合
2. `media/video/<project>/<shot_id>/` に take 採番で配置
3. SHA256で重複排除
4. `assets_manifest.json` を出力

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/assets_manifest.json`

## Rules

- 未照合ショットがある場合は失敗終了
- 元ファイルは削除せず archive へ移動
