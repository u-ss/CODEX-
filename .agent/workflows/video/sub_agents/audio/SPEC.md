# Video Audio Mixer SKILL v1.0.0## フィルタ方針

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- ナレーション: `adelay` + `amix`
- BGM: `volume`, `afade`
- ダッキング: `sidechaincompress`

## 出力仕様

- `mix.wav`（PCM 16bit）
- timeline総尺に合わせる

## Rules

- 無音出力を防ぐQCを行う
- 失敗時はffmpegコマンド要約を残す

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
