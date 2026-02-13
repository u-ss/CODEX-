# 実装エージェント

**目的**: `/code`（7-Phase）でコーディング・デバッグ・検証・ドキュメント同期を行う。

## 📂 フォルダ構成

```
実装エージェント/
├── scripts/         ← 実装用ツール・スクリプト
└── README.md
```

## 🔗 ワークフロー・SKILL（正）

- 論理層: `.agent/workflows/code/`
  - [WORKFLOW.md](../../.agent/workflows/code/WORKFLOW.md)
  - [SKILL.md](../../.agent/workflows/code/SKILL.md)

## 💡 使用方法

1. `/code` ワークフローを起動
2. 7-Phase（RESEARCH→PLAN→TEST→CODE→DEBUG→VERIFY→DOCUMENT）に従って実行
