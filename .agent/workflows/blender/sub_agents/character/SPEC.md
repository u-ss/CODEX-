# Blender Character SKILL v1.0.0## コンセプト

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


`/blender` (character モード) は **キャラ専用オーケストレーター**。

- 仕様の曖昧さを `character_spec` で正規化
- 生成順と対称制約を `character_blueprint` で確定
- `build -> validate -> repair` の反復で品質の下振れを抑制
- 完成後に `self_review` で成果物の再判定を実施
- `--open-gui --interactive` で Blender を開いたまま対話編集

## エージェント部隊（実装）

| 役割 | 実装モジュール | 出力 |
|:-----|:---------------|:-----|
| CharacterSpecAgent | `tools/blender_bridge/character_spec.py` | `character_spec_normalized.json` |
| CharacterBlueprintAgent | `tools/blender_bridge/character_blueprint.py` | `character_blueprint_*.json` |
| CharacterBuildAgent | `tools/blender_bridge/scripts/build_character_v1.py` | `iter_*.blend`, `iter_*_*.png` |
| CharacterValidationAgent | `tools/blender_bridge/scripts/validate_character.py` | `validation_iter_*.json` |
| CharacterLiveEditAgent | `tools/blender_bridge/character_live_intent.py`, `tools/blender_bridge/character_live_repl.py` | `live_edit_log.jsonl`, `final_live.blend` |

## 主要契約

- `tools/blender_bridge/contracts/character_spec.schema.json`
- `tools/blender_bridge/contracts/character_validation.schema.json`

## 仕様ポイント

1. 部位生成
- `target_parts` で部位を限定可能（例: `["hair"]`, `["eye_l", "eye_r"]`）
- 依存アンカー（例: 目→頭）を自動補完

2. 参照画像
- ローカル画像のみ利用（有料APIなし）
- ファイル名から `front/side/back` を推定
- カバレッジ不足は `assumptions` と `run_report.json` に記録

3. 品質ゲート
- 寸法: 幅/奥行/身長
- 部位: 欠落有無
- 左右対称: `_l/_r` ペア差分
- リグ: `require_rig` 条件
- 予算: `poly_budget`

## 代表コマンド

```powershell
# 基本python tools/blender_bridge/character_agent.py --prompt "アニメ風キャラを作って"

# 部位生成python tools/blender_bridge/character_agent.py --prompt "目だけ生成して" --form-json "{""target_parts"":[""eye_l"",""eye_r""]}"

# 参照画像付きpython tools/blender_bridge/character_agent.py `
  --prompt "リアル寄りのキャラを作って" `
  --reference-images ".\refs\char_front.png,.\refs\char_side.png,.\refs\char_back.png"

# GUIライブ編集python tools/blender_bridge/character_agent.py --prompt "キャラを作って" --open-gui --interactive
```

## Rules

- 有料APIは使わない（ローカル解析のみ）
- 部位欠落や対称崩れを黙認しない（`NEEDS_INPUT` を返す）
- run_reportなしで完了扱いにしない
- Language: 日本語

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
