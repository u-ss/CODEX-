# 日記エージェント

メモ帳に追加した情報を取り出しやすい形でMarkdownファイルに保存するエージェント。

## ワークフロー定義（正）

- `.agent/workflows/diary/`
  - `SKILL.md`: 日記管理の技術仕様
  - `WORKFLOW.md`: 実行手順

## 機能

- **追加**: 新しいエントリを作成（タグ・カテゴリ付き）
- **検索**: キーワード・日付・タグでエントリを検索
- **一覧**: 日付範囲でエントリを表示

## 使い方

```powershell
# エントリ追加
python エージェント/日記エージェント/scripts/diary.py add --content "今日の作業内容"

# 今日のエントリ一覧
python エージェント/日記エージェント/scripts/diary.py list --date today

# 検索
python エージェント/日記エージェント/scripts/diary.py search --keyword "MCP"
```

## データ保存先

- `エージェント/日記エージェント/data/entries/` — Markdownエントリファイル
- `エージェント/日記エージェント/data/index.json` — 検索インデックス
