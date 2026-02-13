# Video Asset Collector SKILL v1.0.0## 命名規則

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- 正規名: `<shot_id>_takeNN.<ext>`
- 配置先: `media/video/<project_slug>/<shot_id>/`

## マッチ規則

- 既定: ファイル名に `shot_id` を含む
- 拡張子: `.mp4`, `.mov`, `.mkv`, `.webm`

## 出力

- `assets_manifest.json`
  - `shot_id`
  - `takes[]` (`path`, `original_name`, `sha256`)

## Rules

- 同一SHA256は同一takeとして扱う
- マッチ件数0のショットはエラー

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
