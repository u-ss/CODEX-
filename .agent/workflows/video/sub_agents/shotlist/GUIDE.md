# Video ShotList Validator Workflow v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと

## 概要

`A1` として `shot_list.json` の契約検証と正規化を行う。

## 手順

1. `shot_list.directed.json` があれば優先し、なければ `projects/<project_slug>/shot_list.json` を読む
2. JSON Schema と Pydantic で検証する
3. ID重複/必須欠落/値域異常を検出する
4. 正規化済み `shot_list.normalized.json` を出力する

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/shot_list.normalized.json`（`director_artifacts` が存在する場合は D1 成果物の参照を含む）

## Rules

- 検証エラー時は後続を実行しない
- 正規化後データを以降の正とする
