# Video Remotion Props Builder SKILL v1.0.0## 変換対象

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `timeline.shots[]`
- `fps`, `width`, `height`, `total_frames`
- transition/subtitle/render_src

## 実装要件

- Pathを絶対化
- 文字コードはUTF-8
- Node側に渡すJSONは `ensure_ascii=False`

## Rules

- Remotion側で追加推論しない
- A5で決めた値をそのまま渡す

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
