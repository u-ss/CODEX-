# Video Media Probe SKILL v1.0.0## 測定項目

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `fps`, `duration_sec`, `width`, `height`, `bit_rate`
- `brightness_mean`（簡易）
- `freeze_ratio`（簡易）

## take採点（軽量）

- 解像度適合
- 尺適合
- 輝度レンジ
- ビットレート
- 静止検出

## Conform仕様

- `scale=1920:1080`
- `setsar=1`
- `fps=24`
- `pix_fmt=yuv420p`

## Rules

- すべての`render_src`をConformへ向ける
- `selected_take`決定根拠をJSONに残す

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
