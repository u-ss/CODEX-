---
name: Workflow Lint Agent v1.3.0
description: ワークフロー/スキル/README の整合性 lint 実行手順
---

# Workflow Lint Agent v1.3.0 (`/workflow_lint`)

ワークスペース内のエージェントドキュメント（SKILL.md/WORKFLOW.md/SPEC.md/GUIDE.md/README.md）の整合性を lint し、矛盾・重複・欠落を早期に検出する。

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（判定基準・ルールカタログ・テンプレート定義）

## 📋 Protocol: 7-Step Lint

```
┌─────────────────────────────────────────────────────────────┐
│ 1. PREPARE 🔍                                                │
│    → SKILL.md を読み、ルールカタログを把握                    │
│    → 対象ワークフロー/エージェントの範囲を確認               │
│    ↓                                                        │
│ 2. RUN 🚀                                                    │
│    → workflow_lint.py を実行                                  │
│    ↓                                                        │
│ 3. INTERPRET 📌                                              │
│    → ERROR / CAUTION / ADVISORY を重大度別に整理              │
│    → 各指摘の修正案を作成                                    │
│    ↓                                                        │
│ 4. CONFIRM ✅ 【ユーザー承認必須】                            │
│    → 検出結果と修正案をユーザーに提示                        │
│    → ユーザーが「これとこれを直して」と指示                  │
│    → ⭐ 承認なしに修正しない（auto-fix禁止）                │
│    ↓                                                        │
│ 5. APPLY ✏️                                                  │
│    → ユーザーが承認した項目のみ修正                          │
│    → ドキュメント形式・テンプレートに従って編集              │
│    ↓                                                        │
│ 6. VERIFY ✅                                                 │
│    → 再実行して ERROR=0 を確認                               │
│    → --fail-on-caution で CAUTION=0 も確認（推奨）           │
│    ↓                                                        │
│ 7. REPORT 📝                                                 │
│    → 修正した内容をユーザーに報告                            │
└─────────────────────────────────────────────────────────────┘
```

> [!CAUTION]
> **CONFIRMステップを飛ばしてはならない。** ユーザーの承認なくドキュメントを修正することは禁止。

## 実行コマンド

推奨（ログ + アーティファクト保存）:

```powershell
python .agent/workflows/workflow_lint/scripts/workflow_lint.py
```

元ツールを直接実行（出力はコンソールのみ）:

```powershell
python tools/workflow_lint.py
```

厳格化（運用ゲート）:

```powershell
# CAUTION以上で失敗
python tools/workflow_lint.py --fail-on-caution

# ADVISORY以上で失敗
python tools/workflow_lint.py --fail-on-advisory
```

## 📊 重大度別アクションマトリクス

| 重大度 | アクション | 修正期限 | 例 |
|:-------|:-----------|:---------|:---|
| ERROR | **ユーザー承認後即時修正** | 当該作業中 | SKILL.md欠落、バージョン矛盾 |
| CAUTION | ユーザー承認後原則修正 | 次回作業まで | 事前読了記載漏れ |
| ADVISORY | 推奨 | 任意 | 正本明記なし |

## CONFIRM ステップ詳細

> [!IMPORTANT]
> **auto-fix禁止。** エージェントは検出結果と修正案を提示するのみ。ユーザーが「これを直して」と指示した項目だけをAPPLYステップで修正する。

ユーザーへの提示フォーマット:

```
## Lint結果: N件検出

### ERROR (M件) - 修正必須
1. [WL-VER-002] codex: SKILL=v1.0.0 / WORKFLOW=v1.1.0 バージョン不一致
   → 修正案: SKILL.mdのH1をv1.1.0に更新

### CAUTION (K件) - 原則修正
2. [WL-FILE-003] blender: WORKFLOW.mdにSKILL.md事前読了の記載がない
   → 修正案: [!CAUTION]ブロックを追加

どれを修正しますか？（番号で指定、例: 「1,2を直して」）
```

## 📊 出力

- コンソール: finding一覧 + `[SUMMARY] errors=... cautions=... advisories=...`
- アーティファクト: `_outputs/workflow_lint/<YYYYMMDD>/workflow_lint_report.json`（推奨スクリプト実行時）
- 実行ログ: `_logs/autonomy/workflow_lint/<YYYYMMDD>/`（WorkflowLogger）

## エスカレーション条件

以下の場合は自動修正せず、ユーザーに確認する：

- ドキュメントの役割境界が不明確（SKILL vs WORKFLOW の分担が判断できない）
- バージョン番号の正しい値が判断できない
- 複数エージェント間の正本境界が競合している

## 💡 Rules

- **7-Step順次実行**: PREPARE→RUN→INTERPRET→CONFIRM→APPLY→VERIFY→REPORT
- **SKILL.md事前読了必須**: ルールカタログを把握してから実行
- **auto-fix禁止**: ユーザー承認なしにドキュメントを修正しない
- **CONFIRM必須**: 検出結果を提示し、ユーザーが指示した項目のみ修正
- **読み取り専用lint**: `workflow_lint.py`自体はファイルを変更しない
- **Language**: 日本語
