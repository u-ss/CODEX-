# VOICEVOXエージェント

VOICEVOX音声生成タスクは論理層ワークフローを正とする。

## ワークフロー定義（正）

- `.agent/workflows/voicebox/`
  - `SKILL.md`: 音声生成の技術仕様
  - `WORKFLOW.md`: 実行手順
- `.agent/workflows/video/sub_agents/voicevox/`
  - `SPEC.md`: 動画パイプライン向けTTS仕様
  - `GUIDE.md`: ナレーション生成手順

## 使い方

- 汎用TTSは `.agent/workflows/voicebox/SKILL.md` を先に確認
- 動画用ナレーションは `.agent/workflows/video/sub_agents/voicevox/GUIDE.md` に従って実行
- 接続確認は `python エージェント/VOICEVOXエージェント/scripts/voicevox_agent.py --check`
