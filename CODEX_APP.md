# CODEX App Playbook (antigravity)

この文書は、Codex App（チャット）で antigravity を運用するための補助導線です。  
正は `.agent/workflows/` とし、既存運用は変更しません。

## 0. 絶対ルール（最初に読む）

- 最初に `.agent/RULES.md` を読む
- 実装・デバッグ・検証は `.agent/workflows/code/WORKFLOW.md` と `.agent/workflows/code/SKILL.md` に従う
- Codex 活用の使い分けは `.agent/workflows/codex/WORKFLOW.md` と `.agent/workflows/codex/SKILL.md` を参照する
- Markdown は UTF-8 前提（PowerShell は `Get-Content -Encoding utf8` 推奨）
- デスクトップ自律操作は `.agent/RULES.md` の制約を守る（`browser_subagent` 禁止、CDP 優先）

## 1. まず「契約」を固める（手戻り防止）

`Goal` と `Acceptance Criteria` が未確定のまま実装を開始しない。

### Task Contract Template (copy/paste)

```md
Goal:
Acceptance Criteria (最低3つ):
- 
- 
- 
Non-goals:
- 
Scope (対象ファイル/モジュール候補):
- 
Constraints (OS/言語/禁止事項/期限):
- 
Current behavior (現状):
Desired behavior (期待):
How to verify (コマンド/観点):
- 
```

## 2. どのワークフローに接続する？（選択表）

| 用途 | 接続先 |
| --- | --- |
| 実装・修正 | `.agent/workflows/code/WORKFLOW.md` |
| 設計相談（概念/CLI活用） | `.agent/workflows/codex/WORKFLOW.md` |
| デスクトップ操作 | `.agent/workflows/desktop/WORKFLOW.md` |
| 運用系（deploy/clean/doc-sync/report） | `.agent/workflows/ops/WORKFLOW.md` |
| 調査・根拠収集 | `.agent/workflows/research/WORKFLOW.md` |

## 3. Codex App での進め方（固定手順）

1. Task Contract を確定する（不足項目は質問で埋める）
2. RESEARCH を行う（対象探索、既存規約、根拠取得）
3. PLAN を作る（Targets、Test Strategy、Risk Controls、Rollback）
4. 実装・修正を小さく進める
5. VERIFY を実行する（テスト、lint、受け入れ条件確認）
6. DOCUMENT を同期する（必要な場合）

期待アウトプット:

- 変更対象ファイル一覧
- 受け入れ条件のチェック結果
- 実行コマンドと結果要約
- 残課題（ある場合）

## 4. 依頼文テンプレ（用途別）

### 実装依頼テンプレ

```md
/code
Goal:
Acceptance Criteria:
- 
- 
- 
Scope:
- 
Constraints:
- 
Current behavior:
Desired behavior:
How to verify:
- 
```

### レビュー依頼テンプレ

```md
/codex
Review goal:
Risk areas:
- 
Must-check files:
- 
Output format:
- High / Medium / Low
```

### デバッグ依頼テンプレ

```md
/code
What happened:
Expected:
Repro steps:
1. 
2. 
3. 
Logs/stacktrace:
Environment:
- OS:
- Python/Node version:
- Related tools:
```

## 5. よくある失敗と対策

- Acceptance Criteria が曖昧: 観測可能な条件に書き換える
- Scope が広すぎる: 対象ファイルを先に絞る
- 先に実装してしまう: Contract 完了前は実装を開始しない
- 検証漏れ: Verify コマンドを先に決める
