# Video Remotion Renderer SKILL v1.0.0## 入力

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `remotion_props.json`
- `remotion/src/*`

## 描画要件

- transition: `cut`, `fade`
- subtitle: 2行上限、可読スタイル
- 出力: `draft.mp4`

## Rules

- エントリポイントは固定化して再現性を担保
- CLI引数はシェル依存を避ける

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
