# Video Timing Builder SKILL v1.0.0## 尺計算

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- 基本: `max(narration + post_pad, min_sec)`
- 上限: `max_sec`
- Auto-fit: 全ショット和で最終尺を決定

## ビート同期

- 入力: `bgm.bpm`, `bgm.offset_sec`
- 吸着上限: `±8 frames`

## 字幕最適化

- 句読点優先分割
- 1行上限文字数
- 最大2行

## Rules

- 行頭禁則（句読点始まり）を避ける
- 変換後字幕を `timeline.json` に保持

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
