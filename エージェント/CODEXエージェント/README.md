# CODEXエージェント

CODEX連携タスクは論理層ワークフローを正とする。

## ワークフロー定義（正）

- `.agent/workflows/codex/`
  - `SKILL.md`: CODEX統合の技術仕様
  - `WORKFLOW.md`: 統合実行手順
  - `sub_agents/app/`: CODEXAPPアプリ操作
  - `sub_agents/cli_review/`: CLIレビューエージェント
  - `sub_agents/review/`: 品質評価エージェント

## 使い方

- `.agent/workflows/codex/SKILL.md` を確認
- `.agent/workflows/codex/WORKFLOW.md` に従って送信/取得を実行
- 品質改善ループは `.agent/workflows/codex/sub_agents/review/` に従って実行
