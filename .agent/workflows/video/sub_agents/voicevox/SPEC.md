# Video VoiceVox TTS SKILL v1.0.0## API

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


- `POST /audio_query?text=...&speaker=...`
- `POST /synthesis?speaker=...`

## 保存先

- `media/audio/<project_slug>/narration/<shot_id>.wav`

## manifest項目

- `shot_id`
- `wav_path`
- `duration_sec`
- `audio_query`

## Rules

- `speaker` `speedScale` `pitchScale` などは設定を優先
- ナレーションなしショットは duration=0 で記録

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
