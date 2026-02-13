# Blender Image World SKILL v1.0.0## コンセプト

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


`/blender` (image_world モード) は **画像参照型のワールド生成オーケストレーター**。

- 画像をローカル解析して初期specへ反映
- 部屋型ワールド（床/壁/天井/扉）を手続き生成
- 既存 `universal_agent` の品質ループを再利用
- 最終成果物を専用run directoryへ集約

## 主要コンポーネント

| 役割 | 実装モジュール | 出力 |
|:-----|:---------------|:-----|
| 参照画像正規化/解析 | `tools/blender_bridge/image_world_spec.py` | `reference_analysis` |
| Image-World Spec生成 | `tools/blender_bridge/image_world_spec.py` | `image_world_spec_normalized.json` |
| 実行オーケストレーター | `tools/blender_bridge/image_world_agent.py` | `run_report.json` |
| 生成/検証エンジン | `tools/blender_bridge/universal_agent.py`（委譲） | `final.*`, `validation_final.json` |

## 仕様ポイント

1. 参照画像入力
- `--reference-images` はカンマ区切り / JSON / 配列を受け付ける
- `front/side/top` などのviewはファイル名から自動推定

2. ローカル解析
- 可能なら Pillow を使って明るさ/コントラスト/彩度/色傾向/エッジ密度を抽出
- Pillow未導入時は存在情報だけでフォールバック

3. ワールド初期構成
- 単室ベース（floor/ceiling/4walls/door frame/door panel）
- 詳細度に応じて pillar/altar などを追加
- 暗部屋キーワード時は照明・露出をダーク寄りへ補正

4. 品質
- `universal_agent` の build/validate/repair ループを使用
- `self_review` で最終成果物を再判定

## 代表コマンド

```powershell
# 画像からワールド生成python tools/blender_bridge/image_world_agent.py `
  --prompt "dark and darker風のボス部屋を作って" `
  --reference-images ".\refs\boss_room_front.png,.\refs\boss_room_side.png"

# GUIを開いたまま追い込みpython tools/blender_bridge/image_world_agent.py `
  --prompt "石造りで暗いボス部屋" `
  --reference-images ".\refs\boss_room_front.png" `
  --open-gui --interactive
```

## Rules

- 有料APIは禁止
- 参照画像パスはローカルファイルを使う
- run_reportなしで完了扱いにしない
- Language: 日本語

