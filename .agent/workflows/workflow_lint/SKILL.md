---
name: Workflow Lint Skill v1.3.0
description: ワークフロー/スキル/README lint の技術仕様
---

# Workflow Lint Skill v1.3.0

`.agent/workflows/` と `エージェント/*/README.md` のドキュメント整合性を検証する lint エージェント。

## 役割境界

- この `SKILL.md` は技術仕様（判定基準・ルールカタログ・テンプレート定義）の正本。
- 実行手順は同フォルダの `WORKFLOW.md` を参照。

## 正本境界

| ドキュメント | 役割 | 正本範囲 |
|:-------------|:-----|:---------|
| `SKILL.md`（本ファイル） | 技術契約 | 判定基準、ルールカタログ、テンプレート、バージョン方針 |
| `WORKFLOW.md` | 実行手順 | 実行フロー、コマンド例、修正ループ |
| `tools/workflow_lint.py` | 実装正本 | チェックロジック、CLI定義、終了コード判定 |

> [!IMPORTANT]
> 実装の唯一の正本は `tools/workflow_lint.py`。SKILL.md 内のルール記述は契約であり、実装が乖離した場合は実装を信頼し、ドキュメントを修正する。

## 目的と非目的

### 目的（保証すること）

- ワークフローの必須ファイル（SKILL.md/WORKFLOW.md）の存在検証
- ドキュメント間のバージョン整合性チェック
- 子エージェント（sub_agents/）のファイル命名規約チェック
- ドキュメント役割境界の違反検出
- エンコーディング異常（UTF-8/U+FFFD）の検出

### 非目的（保証しないこと）

- ドキュメントの内容品質（文章の良さ）の評価
- スクリプトの動作テスト（それは `/code` の責務）
- ドキュメントの自動修正（検出と報告のみ）

## ドキュメント役割境界

エージェントのドキュメントは以下の責務で分担する：

| ファイル | 配置場所 | 責務 | 含むべき内容 | 含むべきでない内容 |
|:---------|:---------|:-----|:-------------|:-------------------|
| `SKILL.md` | `.agent/workflows/{name}/` | 技術仕様・契約 | 判定基準、入出力定義、ルール | 実行手順、コマンド例 |
| `WORKFLOW.md` | `.agent/workflows/{name}/` | 実行手順 | フロー図、コマンド例、前提条件 | 判定基準の詳細定義 |
| `SPEC.md` | `sub_agents/{name}/` | 子エージェント技術契約 | 入出力、制約、data属性 | 手順、コマンド例 |
| `GUIDE.md` | `sub_agents/{name}/` | 子エージェント実行ガイド | CLIコマンド例、Phase Flow | 判定基準の詳細定義 |
| `README.md` | `エージェント/{name}/` | 入口案内 | 概要、関連WF参照、セットアップ | 詳細仕様、実装コード |

## ベストプラクティステンプレート

### SKILL.md 推奨構成

```
# {Agent Name} Skill vX.Y.Z
## 役割境界
## 正本境界（実装正本があれば）
## 対象と入力
## 判定基準 / チェックルール
## 出力と終了コード
## Safety Notes
```

### WORKFLOW.md 推奨構成

```
# {Agent Name} Workflow vX.Y.Z
> [!CAUTION] SKILL.md事前読了
## Protocol: N-Step Flow （図）
## 実行コマンド
## 出力
## Rules
```

### SPEC.md（子エージェント）推奨構成

```
# {Sub-Agent Name} SPEC vX.Y.Z
## ドキュメント正本境界
## 対象と入力
## 技術契約（入出力・制約）
## 検証済みセレクタ/API一覧（該当時）
## 既知の注意点
```

### GUIDE.md（子エージェント）推奨構成

```
# {Sub-Agent Name} GUIDE vX.Y.Z
> [!CAUTION] SPEC.md事前読了
## 実行フロー（図）
## コマンド例
## トラブルシューティング
```

## 判定（Finding）

- `[ERROR]`: ブロッキング。修正必須
- `[CAUTION]`: 非ブロッキング。早期修正推奨
- `[ADVISORY]`: 参考情報。品質改善推奨
- `[WARN]`: legacy alias（新規は CAUTION/ADVISORY を使用）

## ルールカタログ

### バージョン整合

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-VER-001 | ERROR | H1バージョンと本文内「vX.Y.Z追加」が矛盾 |
| WL-VER-002 | CAUTION | SKILL.mdとWORKFLOW.mdのH1バージョンが不一致 |

### 必須ファイル

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-FILE-001 | ERROR | SKILL.md が欠落 |
| WL-FILE-002 | ERROR | WORKFLOW.md が欠落 |
| WL-FILE-003 | CAUTION | WORKFLOW.md に SKILL.md 事前読了の記載がない |

### テンプレート準拠

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-TPL-001 | CAUTION | SKILL.md に「役割境界」見出しがない |
| WL-TPL-002 | ADVISORY | WORKFLOW.md に Protocol/フロー図がない |

### 役割境界

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-ROLE-001 | ADVISORY | SKILL.md に実行コマンド例が多数含まれる |
| WL-ROLE-002 | ADVISORY | WORKFLOW.md に判定基準の詳細定義が含まれる |

### 子エージェント規約

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-SUB-001 | ERROR | sub_agents/ に SKILL.md/WORKFLOW.md が存在 |
| WL-SUB-002 | ERROR | SPEC.md の自己参照が「この SKILL.md は」 |
| WL-SUB-003 | ERROR | GUIDE.md の事前読了参照が「SKILL.md を読む」 |
| WL-SUB-004 | CAUTION | 親 SKILL.md の子エージェント参照パスが不在 |

### エンコーディング

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-ENC-001 | ERROR | UTF-8 decode 失敗 |
| WL-ENC-002 | ERROR | 置換文字 U+FFFD を検出 |

### 正本境界

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-SOURCE-001 | ADVISORY | 実装スクリプトがあるのに正本明記がない |

### ログ統合

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-LOG-001 | CAUTION | entrypointに WorkflowLogger 統合がない |

### ドキュメント→コード整合（v1.2.0追加）

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-XREF-001 | ERROR | SKILL/WORKFLOWに記載のスクリプトパスがディスク上に不在 |
| WL-XREF-002 | CAUTION | `__version__`変数とSKILL.md H1バージョンが不一致 |

### スラッシュコマンド実在確認（v1.2.0追加）

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-CMD-001 | CAUTION | ドキュメント中の `/xxx` が .agent/workflows に不在 |

### ディスク→ドキュメント逆引き（v1.2.0追加）

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-DISC-001 | ADVISORY | workflowsに存在するがdocs/architecture.mdに未記載 |

### README形式準拠（v1.3.0追加）

| Rule ID | 重大度 | 判定条件 |
|:--------|:-------|:---------|
| WL-RMD-001 | CAUTION | README.md に H1 タイトルがない |
| WL-RMD-002 | CAUTION | README.md に「ワークフロー定義（正）」節がない |
| WL-RMD-003 | CAUTION | README.md に「使い方（最短）」節がない |
| WL-RMD-004 | CAUTION | README.md に「入出力」節がない |
| WL-RMD-005 | CAUTION | README.md に「注意事項」節がない |

## ルール実装トレーサビリティ

| Rule ID | 実装関数 | 検出メッセージ例 |
|:--------|:---------|:-----------------|
| WL-VER-001 | `_check_inline_version_contradiction()` | `H1=v1.0.0 but body mentions v1.1.0追加` |
| WL-VER-002 | `lint_workflow_dir()` | `version mismatch SKILL=v1.0.0 WORKFLOW=v1.1.0` |
| WL-FILE-001 | `lint_workflow_dir()` | `missing SKILL.md` |
| WL-FILE-002 | `lint_workflow_dir()` | `missing WORKFLOW.md` |
| WL-FILE-003 | `lint_workflow_dir()` | `WORKFLOW.md should mention SKILL.md pre-read` |
| WL-TPL-001 | `_check_template_compliance()` | `SKILL.md missing '役割境界' heading` |
| WL-ROLE-001 | `_check_role_boundary()` | `SKILL.md contains N command blocks` |
| WL-ROLE-002 | `_check_role_boundary()` | `WORKFLOW.md contains N rule tables` |
| WL-SUB-001 | `_lint_sub_agents()` | `SKILL.md must be renamed to SPEC.md` |
| WL-SUB-002 | `_lint_sub_agents()` | `SPEC.md self-reference uses 'SKILL.md'` |
| WL-SUB-003 | `_lint_sub_agents()` | `GUIDE.md references 'SKILL.md' instead of 'SPEC.md'` |
| WL-SUB-004 | `_lint_sub_agents()` | `parent SKILL.md references ...but does not exist` |
| WL-ENC-001 | `read_utf8_checked()` | `utf-8 decode failed` |
| WL-ENC-002 | `read_utf8_checked()` | `contains replacement character U+FFFD` |
| WL-SOURCE-001 | *(v1.3.0予定)* | `正本明記がない` |
| WL-LOG-001 | `lint_workflow_logging_coverage()` | `entrypoint must integrate WorkflowLogger` |
| WL-XREF-001 | `lint_cross_ref_script_paths()` | `script path ... not found on disk` |
| WL-XREF-002 | `lint_cross_ref_version()` | `__version__=... but SKILL H1=...` |
| WL-CMD-001 | `lint_slash_commands()` | `slash command ... has no matching workflow` |
| WL-DISC-001 | `lint_disc_coverage()` | `exists on disk but not mentioned in docs/architecture.md` |
| WL-RMD-001 | `lint_agent_readmes()` | `README.md missing H1 title` |
| WL-RMD-002 | `lint_agent_readmes()` | `README.md missing section '## ワークフロー定義（正）'` |
| WL-RMD-003 | `lint_agent_readmes()` | `README.md missing section '## 使い方（最短）'` |
| WL-RMD-004 | `lint_agent_readmes()` | `README.md missing section '## 入出力'` |
| WL-RMD-005 | `lint_agent_readmes()` | `README.md missing section '## 注意事項'` |

## 出力契約

### Finding 形式

各 finding は以下のフォーマットで出力される:

```
[重大度] <対象パス>: <メッセージ> (<Rule ID>)
```

例:
```
[ERROR] codex: version mismatch SKILL=v1.0.0 WORKFLOW=v1.1.0 (WL-VER-002)
[CAUTION] blender/sub_agents/character: GUIDE.md contains legacy slash command '/video_director' (WL-SUB-003)
```

### サマリー行

```
[SUMMARY] errors=<n> cautions=<n> advisories=<n> legacy_warnings=<n>
```

### 終了コード

| 条件 | 終了コード |
|:-----|:----------|
| `errors > 0` | `1` |
| WF_ROOT不在 | `2` |
| `--fail-on-caution` かつ `cautions > 0` | `1` |
| `--fail-on-advisory` かつ `advisories > 0` | `1` |
| すべてパス | `0` |

### JSON出力（推奨スクリプト経由）

アーティファクト: `_outputs/workflow_lint/<YYYYMMDD>/workflow_lint_report.json`

### CI連携コマンド例

```powershell
# 基本（ERROR のみブロック）
python tools/workflow_lint.py

# 厳格（CAUTION もブロック）
python tools/workflow_lint.py --fail-on-caution
```

## バージョン管理方針

- **正本**: `tools/workflow_lint.py` の `__version__`
- **SemVer**:
  - PATCH: 文言修正・誤字
  - MINOR: 新ルール追加（既存互換）
  - MAJOR: 判定基準変更・終了コード変更
- **同期ルール**: SKILL.md / WORKFLOW.md の H1 は常に実装バージョンと一致

## Safety Notes

- 読み取り専用（`workflow_lint.py` は出力先のみ作成、本体ファイルを変更しない）
- **auto-fix禁止**: エージェントはユーザー承認なしにドキュメントを修正してはならない
- **対話型修正**: 検出結果と修正案を提示 → ユーザーが指示 → 承認された項目のみ修正
- WIP除外は `tools/workflow_lint.py` の `WIP_IGNORE_*` で管理
