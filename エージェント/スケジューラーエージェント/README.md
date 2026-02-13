# スケジューラーエージェント

エージェントの定期実行（スケジュール管理）を担当する部署。

> スケジューリング自体は **外部（Codex Automations / OSスケジューラ）** に任せ、このリポジトリでは実行対象の入口を `/ops` に集約します。

## ワークフロー定義

- 論理層（正）: `.agent/workflows/ops/`
  - `SKILL.md`: 技術仕様
  - `WORKFLOW.md`: 実行手順

## 使い方

```powershell
# 例: 日次でヘルスチェックを回す（外部スケジューラから実行）
python .agent/workflows/ops/scripts/ops.py health

# 例: リリース前にdry-runチェック
python .agent/workflows/ops/scripts/ops.py deploy --dry-run
```
