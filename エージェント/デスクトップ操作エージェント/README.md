# デスクトップ操作エージェント

**目的**: Perceive-Decide-Actループで自律PC操作を行う。

## 📂 フォルダ構成

```
デスクトップ操作エージェント/
├── core/            ← コアモジュール（Circuit Breaker、ScreenKey等）
├── patterns/        ← 設計パターン
├── scripts/         ← ユーティリティスクリプト
└── README.md
```

## 🔗 ワークフロー・SKILL

- 論理層: `.agent/workflows/desktop/`
  - [WORKFLOW.md](../../.agent/workflows/desktop/WORKFLOW.md)
  - [SKILL.md](../../.agent/workflows/desktop/SKILL.md)

## ⚠️ 重要ルール

- **browser_subagent禁止** - BOT判定される
- **CDP最優先** - Playwright connect_over_cdp使用
- **SKILL.md必読** - 実行前に必ず確認
