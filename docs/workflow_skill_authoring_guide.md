# Workflow / Skill Authoring Guide

## Purpose

`.agent/workflows/<workflow>/` 配下の `WORKFLOW.md` と `SKILL.md` の責務を分離し、重複と矛盾を防ぐための執筆ルール。

## Source Of Truth

- `WORKFLOW.md`: 実行手順の正本（何を、どの順番で実行するか）
- `SKILL.md`: 技術仕様の正本（どう実装・判定するか）
- `エージェント/*/README.md`: 導線のみ（仕様の本体を置かない）

## `WORKFLOW.md` に書くこと

- 目的、前提条件、実行フロー（Phase/Step）
- 実行コマンド（CLI例）
- 出力物、完了条件、運用ルール
- `SKILL.md` 事前読了の明記

## `WORKFLOW.md` に書かないこと

- APIスキーマの詳細定義
- 判定閾値の正本テーブル
- 実装コードの詳細説明

上記は `SKILL.md` に集約し、`WORKFLOW.md` はリンクで参照する。

## `SKILL.md` に書くこと

- 入出力契約、アーティファクト仕様
- しきい値、判定基準、検証ロジック
- 技術的制約、依存、実装上の注意
- 必要なコード例（最小限）
- `WORKFLOW.md` 参照（手順はそちらが正本）

## `SKILL.md` に書かないこと

- 実行順序や運用ゲートの主定義
- タスク実行時の手順本文

## Required Blocks

### `WORKFLOW.md` 必須要素

1. Frontmatter（`name`, `description`）
2. H1タイトル（バージョン付き）
3. `SKILL.md` 事前読了の明示
4. 実行手順
5. 出力とルール

### `SKILL.md` 必須要素

1. Frontmatter（`name`、必要に応じて `description`, `capabilities`, `command_template`）
2. H1タイトル（バージョン付き）
3. 役割境界（`WORKFLOW.md` 参照）
4. 技術仕様（契約・判定基準・制約）

## Minimal Templates

### `WORKFLOW.md`

```markdown
---
name: Example Workflow v1.0.0
description: 実行手順
---

# Example Workflow v1.0.0 (`/example`)

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと

## 概要
...

## 実行手順
...

## 出力
...
```

### `SKILL.md`

```markdown
---
name: Example Skill v1.0.0
description: 技術仕様
---

# Example Skill v1.0.0

## 役割境界

- この `SKILL.md` は技術仕様の正本。
- 実行手順は同フォルダの `WORKFLOW.md` を参照。

## 技術仕様
...
```

## Review Checklist

- `WORKFLOW.md` と `SKILL.md` のバージョンは一致しているか
- `WORKFLOW.md` に `SKILL.md` 参照があるか
- `SKILL.md` に `WORKFLOW.md` 参照があるか
- 重複した詳細仕様が `WORKFLOW.md` に残っていないか
- `python tools/workflow_lint.py` が ERROR 0 か

## Lint Severity

- `ERROR`: ブロッキング問題。必須修正（欠落ファイル、参照切れ、ログ統合漏れ、UTF-8破損など）
- `CAUTION`: 要注意。非ブロッキングだが早めの対応を推奨
- `ADVISORY`: 注意喚起。品質・保守性のための改善推奨
- 運用例:
  - `python tools/workflow_lint.py --fail-on-caution`
  - `python tools/workflow_lint.py --fail-on-advisory`
  - `python tools/workflow_lint.py --fail-on-warn`（互換用。`CAUTION/ADVISORY` も失敗扱い）

### WIP 除外

- 作成中で lint から一時除外する対象は `tools/workflow_lint.py` の以下に定義する:
  - `WIP_IGNORE_AGENTS`
  - `WIP_IGNORE_WORKFLOWS`
- 正式運用に入るタイミングで除外対象から外す。
