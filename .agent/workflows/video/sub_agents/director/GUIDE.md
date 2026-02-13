# Video Director Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`D1` として、`shot_list` からStoryboard用のシーン別プロンプトと品質レポートを生成する。

## 手順

1. `shot_list.json` を読み込んでPydantic検証する
2. shotごとに Storyboard prompt / negative prompt / variants を作る
3. 品質指摘を作り `sora_quality_report.json` に保存する
4. `shot_list.directed.json` を作る（既存手入力は安全に保持）
5. `sora_style_guide.md` を作り、人間のSora操作手順を出力する

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/shot_list.directed.json`
- `_outputs/video_pipeline/<project>/<run_id>/sora_prompt_pack.json`
- `_outputs/video_pipeline/<project>/<run_id>/sora_style_guide.md`
- `_outputs/video_pipeline/<project>/<run_id>/sora_quality_report.json`

## Rules

- 有料APIを呼ばない
- `--resume` 時は `run_state.json` の成功状態を尊重する
- `--force` 指定時のみ既存 `video.storyboard` を上書き可能
