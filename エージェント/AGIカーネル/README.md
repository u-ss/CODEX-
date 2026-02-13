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
