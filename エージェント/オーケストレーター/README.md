# オーケストレーター（Orchestrator PM）

計画生成と実行接続（dispatch queue）に特化したオーケストレータエージェント。

## 概要

ユーザーの Goal + Constraints から:
1. **ROADMAP**: フェーズ順ロードマップ
2. **TASK LIST**: 成果物単位のタスク一覧
3. **QUESTIONS**: 計画確定に必要な質問（最大3つ）
4. **MACHINE_JSON**: 機械可読JSON
5. **Dispatch Queue**: 実行接続キュー（`command_hint`）

## 新規エージェント連携

- `capabilities` を持つエージェントは能力ベースで自動割当されます
- `command_template` を設定すると dispatch の `command_hint` が自動生成されます

を出力する。実行はしない（後で/checkに接続可能）。

## ディレクトリ構成

```
エージェント/オーケストレーター/
├── README.md          ← このファイル
└── scripts/
    └── orchestrator_pm.py  ← メインスクリプト
```

## 使い方

```powershell
python エージェント/オーケストレーター/scripts/orchestrator_pm.py --goal "目標" --constraints "制約"
```

実行中は `override> ` プロンプトでオーバーライドを対話入力できます（空行または `done` で終了）。
非対話で使う場合は `--no-interactive-overrides` を指定します。

## ワークフロー

- 論理層: `.agent/workflows/orchestrator_pm/SKILL.md`, `WORKFLOW.md`
- 物理層: `エージェント/オーケストレーター/scripts/`
