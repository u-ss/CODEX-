---
name: Video Agent Workflow v1.1.0
description: 動画制作統合ワークフロー — DAGオーケストレーション
---

# Video Agent Workflow v1.1.0 (`/video`)

**動画制作パイプラインD1+A1〜A9の依存関係を管理し、子エージェントで実行する。**

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと

## 📋 使い方

```
/video                          → パイプライン全体を実行
/video --resume                 → 失敗ステップから再開
/video --from a2_collect        → 特定ステップから再開
```

## 🔄 ワークフロー

```
ユーザー: /video（依頼）
    ↓
子エージェント振り分け（DAG順序で実行）:
    ↓
Step D1: Director（sub_agents/director/SPEC.md）
    → shot_list.directed.json + sora成果物を生成
    → quality_report に severity=error があれば停止
    ↓
Step A1: ShotList（sub_agents/shotlist/SPEC.md）
    → 入力契約を検証
    ↓
Step A2 + A4: 並列実行
    ├─ Asset（sub_agents/asset/SPEC.md）→ 素材収集
    └─ VoiceVox（sub_agents/voicevox/SPEC.md）→ 音声生成
    ↓
Step A3: Probe（sub_agents/probe/SPEC.md）
    → 素材測定/Conform確定
    ↓
Step A5: Timing（sub_agents/timing/SPEC.md）
    → タイムライン確定
    ↓
Step A6: RemotionProps（sub_agents/remotion_props/SPEC.md）
Step A7: Renderer（sub_agents/renderer/SPEC.md）
Step A8: Audio（sub_agents/audio/SPEC.md）
Step A9: Finalize（sub_agents/finalize/SPEC.md）
    ↓
run_state.json を更新
```

## 出力

- `_outputs/video_pipeline/<project>/<run_id>/run_state.json`
- `_logs/video_pipeline/<project>/<run_id>.jsonl`

## Rules

- 実行順序はDAG依存に従う
- `--resume` 時は成功済みステップをスキップ可能
- 有料APIの直接呼び出しは禁止
- 子エージェントのSPEC.mdを読んでから各ステップを実行
- 報告言語は日本語

## 復帰手順（A2 失敗時）

- `missing assets` が出たら、`sora_inbox/` に不足ショット素材を投入
- 再実行: `/video --from a2_collect --resume`
