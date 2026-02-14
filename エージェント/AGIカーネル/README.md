# AGIカーネル

自己改善ループ（AGI Kernel）の物理層。
リポジトリの健全性をスキャンし、改善タスクを1つずつ処理する。

## 📂 構成

```
エージェント/AGIカーネル/
├── README.md              ← このファイル（導線）
└── scripts/
    ├── agi_kernel.py      ← エントリーポイント + オーケストレータ
    ├── state.py           ← State管理 / FileLock / 失敗分類
    ├── scanner.py         ← pytest出力パーサー / Scanner / 候補生成
    ├── executor.py        ← Gemini SDK / パッチ生成・適用・検証
    ├── verifier.py        ← 検証コマンド実行
    └── webhook.py         ← Webhook通知（リトライ/backoff対応）
```

## 🔗 論理層

- 技術仕様: [SKILL.md](../../.agent/workflows/agi_kernel/SKILL.md)
- 実行手順: [WORKFLOW.md](../../.agent/workflows/agi_kernel/WORKFLOW.md)

## 🚀 クイックスタート

```powershell
# ヘルプ
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --help

# 1サイクル（dry-run）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --once --dry-run

# 中断から再開
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --resume --dry-run

# マルチリポジトリ
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --workspaces /repo1 /repo2 --dry-run
```

## ⚡ v0.6.1 新機能

### モジュール化

モノリシックな `agi_kernel.py` を5つのサブモジュールに分割。
後方互換性を維持（`from agi_kernel import Scanner` 等は引き続き動作）。

### マルチリポジトリサポート (`--workspaces`)

```powershell
# 複数ワークスペースを順次スキャン
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --workspaces /repo1 /repo2
```

### 旧SDK完全削除

`google-generativeai`（旧SDK）のサポートを終了。`google-genai` に一本化。

```powershell
pip install google-genai
```

### Webhook堅牢化

- 指数バックオフ + ジッター付きリトライ（最大3回）
- タイムアウト（10秒）
- 429 (Rate Limit) 対応
- 冪等性キー付与

### 構造化ログ統一

全 `print()` を `logging` モジュールに移行。JSON構造化出力に対応。

## ⚡ v0.6.0 新機能

### 常駐モード (`--loop`)

```powershell
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --loop
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --loop --interval 60
```

### 承認ゲート (`--approve`)

```powershell
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --approve
```

### JSON構造化ログ (`--log-json`)

```powershell
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --log-json
```

### Lint重要度フィルタ (`--lint-severity`)

```powershell
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --lint-severity error,caution
```

### Webhook通知 (`--webhook-url`)

```powershell
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --webhook-url "https://discord.com/api/webhooks/..."
```

### LLMコスト追跡

`report.json` に `token_usage` フィールドが追加。

## 🛡️ CLI リファレンス

| フラグ | 説明 | デフォルト |
|--------|------|-----------|
| `--once` | 1サイクルのみ実行して終了 | デフォルト動作 |
| `--loop` | 常駐モード | OFF |
| `--interval N` | `--loop`時のサイクル間隔（秒） | 300 |
| `--resume` | `state.json`から再開 | OFF |
| `--dry-run` | EXECUTE/VERIFYをスキップ | OFF |
| `--auto-commit` | VERIFY成功時に自動commit | OFF |
| `--approve` | パッチ適用前に承認 | OFF |
| `--workspace PATH` | ワークスペースルート | 自動検出 |
| `--workspaces PATH...` | マルチリポジトリ | なし |
| `--llm-model NAME` | LLMモデル名 | `gemini-2.5-flash` |
| `--llm-strong-model NAME` | 強力LLMモデル | `gemini-2.5-pro` |
| `--webhook-url URL` | Webhook通知先 | なし |
| `--lint-severity LEVELS` | Lint取込レベル（カンマ区切り） | `error` |
| `--log-json` | JSON構造化ログ出力 | OFF |

