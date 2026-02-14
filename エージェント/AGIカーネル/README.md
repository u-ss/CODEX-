# AGIカーネル

自己改善ループ（AGI Kernel）の物理層。
リポジトリの健全性をスキャンし、改善タスクを1つずつ処理する。

## 📂 構成

```
エージェント/AGIカーネル/
├── README.md          ← このファイル（導線）
└── scripts/
    └── agi_kernel.py  ← メインスクリプト
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
```

## ⚡ v0.6.0 新機能

### 常駐モード (`--loop`)

```powershell
# 5分間隔で自動繰り返し（デフォルト300秒）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --loop

# 間隔を60秒に変更
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --loop --interval 60

# Ctrl+C で安全停止
```

### 承認ゲート (`--approve`)

```powershell
# パッチ適用前に確認プロンプト
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --approve
```

### JSON構造化ログ (`--log-json`)

```powershell
# ログ出力をJSON形式にする（パイプ処理向け）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --log-json
```

### Lint重要度フィルタ (`--lint-severity`)

```powershell
# ERROR + CAUTION を取り込む
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --lint-severity error,caution

# ERROR + CAUTION + ADVISORY
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --lint-severity error,caution,advisory
```

### Webhook通知 (`--webhook-url`)

```powershell
# サイクル完了/PAUSED時にDiscord/Slack互換Webhookで通知
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --webhook-url "https://discord.com/api/webhooks/..."
```

### LLMコスト追跡

`report.json` に `token_usage` フィールドが追加され、入出力トークン数と推定コスト（USD）が記録されます。

```json
{
  "token_usage": {
    "prompt": 1500,
    "output": 800,
    "total": 2300,
    "estimated_cost_usd": 0.000705
  }
}
```

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
| `--llm-model NAME` | LLMモデル名 | `gemini-2.5-flash` |
| `--llm-strong-model NAME` | 強力LLMモデル | `gemini-2.5-pro` |
| `--webhook-url URL` | Webhook通知先 | なし |
| `--lint-severity LEVELS` | Lint取込レベル（カンマ区切り） | `error` |
| `--log-json` | JSON構造化ログ出力 | OFF |
