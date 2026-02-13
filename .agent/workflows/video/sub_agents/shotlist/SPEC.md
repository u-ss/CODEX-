# Video ShotList Validator SKILL v1.0.0## 入力契約

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `schema_version`
- `project_slug`
- `settings`
- `shots[]`
- 優先入力: `shot_list.directed.json`（D1生成時）

## 検証項目

- `shot.id` の重複禁止
- `fps` `resolution` `timing` の値域
- `transition` と字幕設定の整合

## 出力仕様

- デフォルト値を補完した正規化JSON
- `director_artifacts` がある場合は D1 成果物の参照を保持
- エラー時は `field`, `reason`, `hint` を返す

## Rules

- スキーマ互換性を維持する
- 型の曖昧変換を行わない

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
