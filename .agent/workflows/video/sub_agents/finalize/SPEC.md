# Video Finalize SKILL v1.0.0## 入力

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `draft.mp4`
- `mix.wav`

## 出力

- `exports/final.mp4`
- `qc_warnings`（必要時）

## フォールバック

1. loudnorm付きmuxを試行
2. 失敗時はloudnormなしmux
3. 両方失敗時は処理停止

## Rules

- 画質劣化回避のため映像は可能なら再エンコードしない
- 音量正規化の成否を記録する

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
