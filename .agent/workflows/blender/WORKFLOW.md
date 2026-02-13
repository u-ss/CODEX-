---
name: Blender Agent v1.2.0
description: Blender 5.0汎用生成ワークフロー（仕様正規化・アセット選定・反復検証・専門エージェント委譲）
---

# Blender Agent Workflow v1.2.0 (`/blender`)

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SKILL.md` を読んでから実行

## 概要

`/blender` は汎用3D生成の入口。

実行フロー:

1. 要件正規化（domain auto routing）
2. 外部アセット候補選定（ライセンスガード）
3. 生成（手続き + 可能なら外部アセット取込）
4. 寸法/トポロジ/予算検証
5. 自動修正（最大3回）
6. 自己レビュー（成果物/検証JSONの再判定）
7. 成果物確定（blend + 3render + validation + report）

> 戸建て高精度生成は `sub_agents/house/SPEC.md`、キャラ高精度生成は `sub_agents/character/SPEC.md`、画像参照型ワールド生成は `sub_agents/image_world/SPEC.md` を子エージェントとして格納。`domain=house/character` では優先利用する。

## 前提条件

- Blender 5.0.1 がインストール済み
- `.agent/RULES.md` を確認済み
- SKILL.md を確認済み

## ワークフロー

// turbo-all

### Step 1: 環境確認

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --version
python tools/blender_bridge/universal_agent.py --help
```

### Step 2: 汎用生成（Promptのみ）

```powershell
python tools/blender_bridge/universal_agent.py `
  --prompt "モダンなデスクランプを作って" `
  --work-dir ag_runs
```

### Step 2.5: 事前検証（dry-run）

```powershell
python tools/blender_bridge/universal_agent.py `
  --prompt "木製チェアを作って" `
  --asset-manifest .\asset_manifest.json `
  --dry-run `
  --work-dir ag_runs
```

### Step 3: 外部アセット込み生成（推奨）

```powershell
python tools/blender_bridge/universal_agent.py `
  --prompt "工業風の作業部屋シーン" `
  --domain scene `
  --asset-manifest .\asset_manifest.json `
  --allow-licenses "CC0,CC-BY,ROYALTYFREE,INTERNAL" `
  --work-dir ag_runs
```

### Step 4: GUIを開いたまま対話編集

```powershell
python tools/blender_bridge/universal_agent.py `
  --prompt "ショールーム用のプロダクトモデル" `
  --open-gui `
  --interactive `
  --work-dir ag_runs
```

### Step 5: 戸建て専門モード（高精度優先）

```powershell
python tools/blender_bridge/house_agent.py `
  --prompt "戸建てを作って" `
  --work-dir ag_runs
```

### Step 6: キャラ専門モード（高精度優先）

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "ヒューマノイドキャラを作って" `
  --reference-images ".\refs\char_front.png,.\refs\char_side.png,.\refs\char_back.png" `
  --work-dir ag_runs
```

## 出力

- `ag_runs/universal_agent_*/asset_spec_normalized.json`
- `ag_runs/universal_agent_*/iter_*.blend`
- `ag_runs/universal_agent_*/iter_*_front.png`
- `ag_runs/universal_agent_*/iter_*_oblique.png`
- `ag_runs/universal_agent_*/iter_*_bird.png`
- `ag_runs/universal_agent_*/validation_iter_*.json`
- `ag_runs/universal_agent_*/final.blend`
- `ag_runs/universal_agent_*/final_front.png`
- `ag_runs/universal_agent_*/final_oblique.png`
- `ag_runs/universal_agent_*/final_bird.png`
- `ag_runs/universal_agent_*/validation_final.json`
- `ag_runs/universal_agent_*/run_report.json`
- `ag_runs/universal_agent_*/live_session/*`（`--open-gui`時）

## Rules

- 外部アセット利用時はライセンス allow/deny を必ず指定する
- ライセンス不明・非許可アセットは取り込まない
- 検証未達は `NEEDS_INPUT` として終了し、黙って品質劣化しない
- 既存RPC/CLI公開APIは維持する
- Language: 日本語で報告
