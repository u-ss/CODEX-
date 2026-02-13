---
name: Codex Agent Workflow v1.0.0
description: CODEX統合ワークフロー — CODEXAPP操作・品質評価・CLIレビュー
---

# Codex Agent Workflow v1.0.0 (`/codex`)

**CODEX関連の全操作を1つの入口で実行する統合エージェント。**

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと

## 📋 使い方

| 入力例 | 実行される子エージェント |
|:-------|:------------------------|
| `/codex CODEXAPPにメッセージを送って` | App Control |
| `/codex このエージェントを品質評価して` | Review |
| `/codex git差分をレビューして` | CLI Review |
| `/codex --app send "質問"` | App Control（明示指定） |
| `/codex --review /research 95` | Review（明示指定） |
| `/codex --cli-review -Uncommitted` | CLI Review（明示指定） |

## 🔄 ワークフロー

```
ユーザー: /codex（依頼）
    ↓
意図を解析:
    ├─ CODEXAPP操作（送信/応答取得/新スレッド）
    │   → sub_agents/app/SPEC.md を読んで実行
    │
    ├─ 品質評価・自律改善ループ
    │   → sub_agents/review/SPEC.md を読んで実行
    │
    └─ git差分レビュー・自動修正
        → sub_agents/cli_review/SPEC.md を読んで実行
    ↓
結果をユーザーに返す
```

## 📊 ルーティング優先順位

| 優先度 | 条件 | 振り分け先 |
|:-------|:-----|:-----------|
| 1（最高） | `--app`, `--review`, `--cli-review` 明示指定 | 指定された子エージェントへ直接 |
| 2 | テキストに「送信」「メッセージ」「質問して」等 | App Control |
| 3 | テキストに「品質」「評価」「スコア」「点」等 | Review |
| 4 | テキストに「diff」「レビュー」「git」等 | CLI Review |
| 5（最低） | 判定不能 | ユーザーに確認を求める |

> [!IMPORTANT]
> 複合依頼（例:「CODEXに送信して結果をレビューして」）は**順序実行**として扱い、各フェーズの結果を次のフェーズに渡す。

> [!NOTE]
> **キーワード競合時の決定規則**: 複数の優先度にマッチする場合、**より高い優先度**が勝つ。例:「品質レビュー」は「レビュー」(CLI Review, 優先度4)と「品質」(Review, 優先度3)の両方にマッチするが、**優先度3のReviewが選択される**。判断に迷う場合はユーザーに確認する。

## 💡 Antigravityへの指示

1. `/codex` を受けたら、上記優先順位に従い子エージェントのSPEC.mdを読んで実行
2. 明示的なオプション（`--app`, `--review`, `--cli-review`）がある場合は直接振り分け
3. CODEXAPPが起動していない場合は、起動手順を案内
4. 子エージェント間で共通する設定（ポート等）は親SKILL.mdの値を使用

## Rules

- 子エージェントのSPEC.mdを読んでから実行
- CODEXAPP起動確認を忘れない
- Language: 日本語
