# Blender Character Workflow v1.0.0 

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SPEC.md` を読んでから実行

## 概要

キャラモデリング専用の高精度モード。親 `/blender` から `domain=character` で呼ばれる。

固定フロー:

1. 仕様正規化（自然文 + フォーム + 参照画像）
2. 設計図生成（部位順序 / 左右対称ペア / 参照画像カバレッジ）
3. 生成（部位パーツ + 必要時リグ + 3視点レンダ）
4. 検証（寸法 / 部位欠落 / 左右対称 / リグ / ポリ予算）
5. 修正適用して再生成（最大3回）
6. 自己レビュー（成果物/検証JSONの再判定）
7. `final.*` と `run_report.json` を確定

## 前提条件

- Blender 5.0.1 がインストール済み
- `.agent/RULES.md` を確認済み
- SKILL.md を確認済み

## 実行コマンド

### Promptのみ（全身生成）

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "アニメ風のヒューマノイドキャラを作って" `
  --work-dir ag_runs
```

### 部位生成（髪だけ / 目だけ）

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "髪だけ生成して" `
  --form-json "{""target_parts"":[""hair""]}" `
  --work-dir ag_runs
```

### 参照画像あり（no-paid-API）

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "リアル寄りのヒューマノイドを作って" `
  --reference-images ".\refs\char_front.png,.\refs\char_side.png,.\refs\char_back.png" `
  --work-dir ag_runs
```

### 外部アセット込み（ライセンス制御）

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "ゲーム用キャラを作って" `
  --asset-manifest .\asset_manifest.json `
  --allow-licenses "CC0,CC-BY,ROYALTYFREE,INTERNAL" `
  --work-dir ag_runs
```

### GUIを開いたまま対話編集

```powershell
python tools/blender_bridge/character_agent.py `
  --prompt "キャラを作って" `
  --open-gui `
  --interactive `
  --work-dir ag_runs
```

## 出力

- `ag_runs/character_agent_*/character_spec_normalized.json`
- `ag_runs/character_agent_*/character_blueprint_normalized.json`
- `ag_runs/character_agent_*/character_spec_iter_*.json`
- `ag_runs/character_agent_*/character_blueprint_iter_*.json`
- `ag_runs/character_agent_*/iter_*.blend`
- `ag_runs/character_agent_*/iter_*_front.png`
- `ag_runs/character_agent_*/iter_*_oblique.png`
- `ag_runs/character_agent_*/iter_*_bird.png`
- `ag_runs/character_agent_*/validation_iter_*.json`
- `ag_runs/character_agent_*/final.blend`
- `ag_runs/character_agent_*/final_front.png`
- `ag_runs/character_agent_*/final_oblique.png`
- `ag_runs/character_agent_*/final_bird.png`
- `ag_runs/character_agent_*/validation_final.json`
- `ag_runs/character_agent_*/run_report.json`
- `ag_runs/character_agent_*/run_report.json` 内 `self_review`
- `ag_runs/character_agent_*/live_session/*`（`--open-gui`時）

## Rules

- 有料APIは使わない（ローカル処理のみ）
- 参照画像はローカルファイルを使う（front/side/back 推奨）
- ライセンス不明アセットはデフォルト拒否
- 検証未達時は `NEEDS_INPUT` で明示終了
- 既存 `ag_rpc_client.py` の公開APIは変更しない
- Language: 日本語で報告
