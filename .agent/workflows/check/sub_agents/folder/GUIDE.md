# Folder Check v1.1.0 

**自動解析**: 起動時にワークスペース全体を走査し、フォルダ構造・エージェント構造・コードベースを把握する。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SPEC.md`を読んでから実行

> [!IMPORTANT]
> **Antigravityが直接実行する**: ユーザーに対象フォルダを聞かない。ワークスペースルートを自動対象とする。

## 📋 実行フロー

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: SCAN（スキャン）                                  │
│     → folder_analyzer.py --workspace を実行                  │
│     → ワークスペース全体を再帰走査                          │
│     → report.json / report.md を生成                        │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: AGENT MAP（エージェント構造検出）                  │
│     → folder_analyzer.py --agent-map を実行                  │
│     → .agent/workflows/ と エージェント/ の対応関係を検出   │
│     → agent_map.json を生成                                  │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: UNDERSTAND（理解・把握）                           │
│     → 生成されたレポートを読み取る                          │
│     → エージェント構造・コードベースの全体像を把握          │
│     → 把握した情報をユーザーに報告                          │
├─────────────────────────────────────────────────────────────┤
│  Phase 4: ACTIVATE（知識活用）                               │
│     → 把握した情報をコンテキストとして保持                  │
│     → ユーザーの後続質問・タスクに活用可能                  │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 使用ツール

| Phase | ツール |
|:------|:-------|
| SCAN | `run_command`（`folder_analyzer.py --workspace`） |
| AGENT MAP | `run_command`（`folder_analyzer.py --agent-map`） |
| UNDERSTAND | `view_file`（生成レポートの読み取り） |
| ACTIVATE | コンテキスト保持（後続タスクで活用） |

## 📊 出力ファイル

| ファイル | 内容 |
|:---------|:-----|
| `_outputs/folder-check/latest/report.json` | ファイル一覧＋解析結果 |
| `_outputs/folder-check/latest/report.md` | 人間が読めるMarkdownレポート |
| `_outputs/folder-check/latest/agent_map.json` | エージェント構造マップ |

## 💡 Rules

- **Phase 1→2→3→4 順次実行**
- **起動時に自動実行**: ユーザーに対象フォルダを聞かない
- **読み取り専用**: ファイル変更なし
- **Language**: 日本語でレポート出力
