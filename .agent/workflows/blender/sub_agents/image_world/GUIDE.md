# Blender Image World Workflow v1.0.0 

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SPEC.md` を読んでから実行

## 概要

「画像を見せて同系統の空間を作る」ための専用モード。親 `/blender` から呼ばれる。

固定フロー:

1. 参照画像の正規化（path/view）
2. 画像統計の抽出（明るさ/コントラスト/色傾向/エッジ密度）
3. `image_world_spec` 生成（部屋寸法、壁/床/扉、照明、質感方向）
4. `universal_agent` に委譲して生成 + 検証ループ
5. 最終成果物を `image_world_agent_*` 配下へ確定

## 実行コマンド

```powershell
python tools/blender_bridge/image_world_agent.py `
  --prompt "dark and darker風のボス部屋を作って" `
  --reference-images ".\refs\boss_room_front.png,.\refs\boss_room_side.png" `
  --work-dir ag_runs
```

GUIを開いたまま調整する場合:

```powershell
python tools/blender_bridge/image_world_agent.py `
  --prompt "暗い石造りのボス部屋を作って" `
  --reference-images ".\refs\boss_room_front.png" `
  --open-gui `
  --interactive `
  --work-dir ag_runs
```

仕様だけ確認する場合:

```powershell
python tools/blender_bridge/image_world_agent.py `
  --prompt "重厚な地下聖堂を作って" `
  --reference-images ".\refs\cathedral_room.png" `
  --dry-run `
  --work-dir ag_runs
```

## 出力

- `ag_runs/image_world_agent_*/image_world_spec_normalized.json`
- `ag_runs/image_world_agent_*/delegate_form.json`
- `ag_runs/image_world_agent_*/final.blend`
- `ag_runs/image_world_agent_*/final_front.png`
- `ag_runs/image_world_agent_*/final_oblique.png`
- `ag_runs/image_world_agent_*/final_bird.png`
- `ag_runs/image_world_agent_*/validation_final.json`
- `ag_runs/image_world_agent_*/run_report.json`

## 失敗時の扱い

- 生成未達: `status=NEEDS_INPUT`
- 委譲失敗: `status=FAILED`
- いずれも `run_report.json` に理由を残す

## Rules

- 有料APIは使わない（ローカル解析のみ）
- 参照画像が不足しても停止せず、仮定を `assumptions` に記録
- 既存RPC/CLI公開APIは変更しない
- Language: 日本語

