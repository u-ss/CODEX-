---
name: Codex Agent v1.0.0
description: CODEX統合エージェント — CODEXAPP操作・品質評価・CLIレビューを統括
capabilities: codex, cdp, review, auto-fix
---

# Codex Agent SKILL v1.0.0

**CODEXに関連する全操作を統括する統合エージェント。**

## 役割境界

- この SKILL.md はCODEX統合の技術仕様と子エージェント管理の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。
- 各専門処理は `sub_agents/` 配下の子エージェントに委譲する。

## 📖 概要

CODEX関連の3つの機能（アプリ操作・品質評価・CLIレビュー）を1つの入口から利用可能にする。

## 🧩 子エージェント一覧

| 子エージェント | パス | 役割 |
|:---------------|:-----|:-----|
| App Control | `sub_agents/app/SPEC.md` | CDP経由でCODEXAPPを操作（メッセージ送信・応答取得） |
| Review | `sub_agents/review/SPEC.md` | CODEXAPPで任意エージェントの品質を評価し95点まで自律改善 |
| CLI Review | `sub_agents/cli_review/SPEC.md` | Codex CLIでgit差分をチャンク単位レビュー＆自動修正 |

## 共通前提

### CODEXAPP起動

```powershell
# CODEXAPP をCDP付きで起動
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "Codex-Windows\scripts\run.ps1" `
  -Reuse -CdpPort 9224
```

### ポート規約

| ポート | 用途 |
|:-------|:-----|
| 9222 | ❌ Chrome予約（使用禁止） |
| 9223 | CODEXAPP（手動用） |
| **9224** | **CODEXAPP（エージェント用）** |

### CdpClient API

```python
import sys
sys.path.insert(0, '.agent/workflows/codex/sub_agents/app/scripts')
from codexapp_cdp_client import CdpClient, send_message, poll_response, generate_token
```

## Rules

- **ポート9224固定**（9222は使用禁止）
- **ProseMirrorを操作**（textareaは使わない）
- **応答取得はトークン追跡方式**
- **Language**: 日本語
