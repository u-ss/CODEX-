---
name: 日記エージェント v1.0.0
description: メモ帳に追加した情報を取り出しやすい形で保存してほしいね。
---

# 日記エージェント SKILL v1.0.0

**ローカルMarkdownベースのメモ帳/日記/タスク管理エージェント**

## 役割境界

- この SKILL.md は技術仕様（データフォーマット・操作一覧・検索仕様）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。

## 📖 概要

ローカルファイルシステム上でメモ・日記・タスクを管理する。
各エントリーは **Markdown + YAML frontmatter** 形式で保存し、
ANTIGRAVITYから `/diary` で情報追加・取得できる。

---

## 📂 データ構造

```
_data/diary/
├── diary/          # 日記エントリー
├── task/           # タスクメモ
├── note/           # 汎用メモ
└── index.json      # 全エントリーの高速検索用インデックス
```

## 📝 ファイルフォーマット

各エントリーは `.md` ファイル（YAML frontmatter付き）:

```markdown
---
id: "20260212_014900_a1b2"
type: "diary"
title: "今日の振り返り"
tags: ["振り返り", "開発"]
created: "2026-02-12T01:49:00+09:00"
updated: "2026-02-12T01:49:00+09:00"
status: "active"
---

今日はUnity MCPの調査を行った。
```

### フィールド定義

| フィールド | 型 | 必須 | 説明 |
|:-----------|:---|:----:|:-----|
| `id` | string | ✅ | ユニークID（`YYYYMMDD_HHMMSS_xxxx`） |
| `type` | enum | ✅ | `diary` / `task` / `note` |
| `title` | string | ✅ | エントリーのタイトル |
| `tags` | list[str] | - | タグ一覧 |
| `created` | ISO8601 | ✅ | 作成日時 |
| `updated` | ISO8601 | ✅ | 更新日時 |
| `status` | enum | - | `active` / `done` / `archived`（taskで使用） |

### index.json

メタデータ集約ファイル（高速検索用）。
各エントリーのfrontmatter情報 + ファイルパスを配列で保持。

```json
[
  {
    "id": "20260212_014900_a1b2",
    "type": "diary",
    "title": "今日の振り返り",
    "tags": ["振り返り", "開発"],
    "created": "2026-02-12T01:49:00+09:00",
    "updated": "2026-02-12T01:49:00+09:00",
    "status": "active",
    "file": "diary/20260212_014900_a1b2.md"
  }
]
```

---

## 🔧 操作一覧

| コマンド | 説明 |
|:---------|:-----|
| `add` | 新規エントリー追加 |
| `search` | キーワード検索（タイトル・タグ・本文） |
| `list` | フィルタ付き一覧表示 |
| `get` | 特定エントリーの内容取得 |
| `edit` | エントリーの内容更新 |
| `done` | タスクを完了に変更 |
| `today` | 今日のエントリー一覧 |
| `summary` | 最近のエントリー要約出力 |
| `rebuild-index` | index.jsonを全ファイルから再構築 |
| `delete` | エントリー削除（archiveに変更） |

### 操作詳細

#### add
```
python diary.py add --title "タイトル" --type diary --tags "tag1,tag2" --body "本文"
```
- Markdownファイルを `_data/diary/{type}/{id}.md` に生成
- index.json に追加
- `--body` 省略時は空本文で作成

#### search
```
python diary.py search "キーワード" [--type diary] [--tags "tag1"]
```
- index.json のタイトル・タグを検索
- ヒットしたファイルの本文も検索
- 結果をJSON形式で出力

#### list
```
python diary.py list [--type task] [--status active] [--limit 20] [--tags "tag1"]
```
- フィルタなし: 最新20件
- `--type`: タイプ絞り込み
- `--status`: ステータス絞り込み
- `--tags`: タグ絞り込み

#### get
```
python diary.py get <id>
```
- 特定エントリーのfrontmatter + 本文を出力

#### today
```
python diary.py today
```
- 今日作成されたエントリーの一覧

#### done
```
python diary.py done <id>
```
- `status` を `done` に変更、`updated` を更新

#### summary
```
python diary.py summary [--days 7] [--type diary]
```
- 直近N日のエントリー一覧をコンパクトにまとめて出力

#### rebuild-index
```
python diary.py rebuild-index
```
- 全.mdファイルを走査してindex.jsonを再構築

---

## 💡 Rules

- **ファイル名 = ID**: `{id}.md`
- **index.json自動更新**: 追加/編集/削除時に必ず更新
- **タイムゾーン**: JST (UTC+9) で統一
- **エンコーディング**: UTF-8
- **Language**: 日本語
