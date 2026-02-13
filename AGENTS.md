# Codex / AI Agent Instructions (antigravity)

## 目的

このリポジトリは **`.agent/workflows/` を正**として、各エージェントの **WORKFLOW.md（手順）** と **SKILL.md（技術詳細）** を運用しています。Codex（CLI/App）で作業する際も、原則このワークフローに沿って進めてください。

## 最重要ルール

- **必ず最初に読む**: `.agent/RULES.md`（強制ルール）
- **入口の原則**: 明示的な `/xxx` 指定がない場合は、まず `/orchestrator_pm` で計画・割当を確定してから実行へ進む
- コーディング/デバッグ/検証など **実装タスク**:
  - `.agent/workflows/code/SKILL.md`
  - `.agent/workflows/code/WORKFLOW.md`
- 文字化け防止: Markdown は **UTF-8** 前提（PowerShell では `Get-Content -Encoding utf8` を推奨）

## 入口（よく使うコマンド）

- ワークフロー lint:
  - `python tools/workflow_lint.py`
- リポジトリ衛生チェック:
  - `python tools/repo_hygiene_check.py`
- Check Agent CLI:
  - `python .agent\\workflows\\check\\scripts\\check.py --help`

## 実装タスクの進め方（要約）

通常は `/orchestrator_pm` で計画・割当を作成し、実装フェーズで `/code` ワークフローの 7-Phase（RESEARCH→PLAN→TEST→CODE→DEBUG→VERIFY→DOCUMENT）を順番に実行します。必要に応じてドキュメント同期まで行います（詳細は `.agent/workflows/code/WORKFLOW.md`）。

## デスクトップ操作タスクの注意

デスクトップ自律操作系のタスクは `.agent/RULES.md` の制約に従ってください（例: `/desktop` では `browser_subagent` を使わない、CDP優先など）。
