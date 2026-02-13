---
name: 日記エージェント v1.0.0
description: メモ帳に追加した情報を取り出しやすい形で保存してほしいね。
---

# 日記エージェント v1.0.0 (`/diary`)

**ローカルMarkdownベースのメモ帳/日記/タスク管理**。YAML frontmatter付きMarkdownで保存し、CLIで追加・検索・取得する。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SKILL.md`を読んでから実行

## 📋 使い方

Antigravityが `/diary` を受けたら、ユーザーの意図に応じて以下のコマンドを実行する。

### スクリプトパス

```
python エージェント/日記エージェント/scripts/diary.py <command> [options]
```

### コマンド一覧

| 意図 | コマンド例 |
|:-----|:-----------|
| メモを追加 | `add --title "タイトル" --type note --body "内容"` |
| 日記を書く | `add --title "今日の日記" --type diary --tags "振り返り" --body "内容"` |
| タスクを追加 | `add --title "○○をやる" --type task --tags "開発"` |
| 検索する | `search "キーワード"` |
| 一覧を見る | `list [--type diary] [--limit 10]` |
| 特定のメモを見る | `get <id>` |
| タスクを完了にする | `done <id>` |
| 今日のメモ一覧 | `today` |
| 最近のまとめ | `summary [--days 7]` |
| インデックス修復 | `rebuild-index` |

## 📂 データ保存先

```
_data/diary/
├── diary/     # 日記
├── task/      # タスク
├── note/      # 汎用メモ
└── index.json # 検索インデックス
```

## 🔄 ワークフロー

```
ユーザー: /diary（自然言語で依頼）
    ↓
Antigravityが意図を解析:
    ├─ 追加系 → add コマンド実行
    ├─ 検索系 → search / list / today コマンド実行
    ├─ 取得系 → get コマンド実行
    ├─ 更新系 → edit / done コマンド実行
    └─ 要約系 → summary コマンド実行
    ↓
結果をユーザーに返す（日本語）
```

## 💡 Antigravityへの指示

1. ユーザーが `/diary` で何か依頼したら、意図に合ったコマンドを組み立てて実行
2. `--body` の内容が長い場合は、一時ファイルに書き出してから `--body-file` で渡す
3. 検索結果が多い場合は上位5件を表示し「他にもN件あります」と伝える
4. 自然言語の依頼からタグを推測して自動付与してよい

## 💡 Rules

- **Language**: 日本語で応答
- ファイル操作後は必ずindex.jsonも更新される（diary.pyが自動処理）
- エラー時はユーザーに原因と対処法を日本語で報告
