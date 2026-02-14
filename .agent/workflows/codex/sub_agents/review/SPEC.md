# CODEXAPP Review Agent SKILL v2.0.0**技術詳細**: 任意のエージェントに対して評価タスクを設定し、実行ログをCODEXAPPに送信してスコアフィードバックをもとに自律改善するループの技術仕様。

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


## 🎯 概要

CODEXAPPは GPT-5.3-Codex が稼働するレビュー環境。エージェントの出力品質を**2軸**で採点する:

| 評価軸 | 内容 | 満点 |
|:-------|:-----|:----:|
| **エージェント品質** | エージェントの出力内容の正確性・根拠・論理構成 | 100 |
| **ログ適切性** | 実行ログの構造・整合性・監査可能性 | 100 |

このスキルは「タスク定義→実行→ログ収集→CODEXAPP送信→分析→修正」のループを回し、**両軸95点以上**に到達するまで自律反復する。

## 🧩 対象エージェントの要件

CODEXAPPで評価可能なエージェントは以下を満たすこと:

```
必須要件:
□ Pythonスクリプトで実行可能（exit code管理あり）
□ 構造化ログを出力する（JSON/JSONL/テキスト）
□ 実行結果のサマリーまたはレポートを出力する
□ 再実行可能（冪等性があること）

推奨要件:
□ 多層ログ構造（サマリー + 詳細 + 構造化ログ）
□ メトリクス/スコアの自己計算機能
□ 検証(validation)フェーズを持つ
```

## 📐 ログ整合性モデル

エージェントが多層ログを持つ場合、**全レイヤーの値が完全一致していること**が最重要:

| レイヤー | 典型ファイル | 役割 |
|:---------|:------------|:-----|
| L1: サマリー | `*_summary.json` | 実行概要・最終スコア・違反一覧 |
| L2: 詳細レポート | `*_metadata.json` or `*_report.json` | 品質詳細・ソース・因果ログ |
| L3: 構造化ログ | `*.jsonl` or `*.log` | フェーズ別実行トレース（機械監査用） |

### 整合性チェック原則

```
対象エージェントに応じて以下を検証:
□ L1の集計値 == L2の詳細値の合計
□ L2のメトリクス == L3のイベントから再計算した値
□ 成果物(MD/HTML等)内の記載値 == L1/L2/L3の実数値
□ タイムスタンプの論理的整合性（L3のイベント順序）
□ エラーカウント/警告数の一貫性
```

> [!CAUTION]
> **ハードコード値の罠**: 成果物内に数値を直書きすると、再実行時にログと乖離する。必ず実行時メトリクスから動的生成すること。

## 🤖 CODEXAPP事前相談プロトコル

Phase 0で対象エージェントに最適な**ログ取得方法**と**評価タスク**をCODEXAPPに相談して決定する。

### なぜ相談が必要か

エージェントごとにログ形式・評価基準・成功指標が異なる:

| エージェント | ログ形式 | 評価重点 |
|:------------|:---------|:--------|
| research | summary+metadata+JSONL (3層) | 根拠の充実度、claim追跡 |
| desktop | スクリーンショット+操作ログ | 操作精度、成功率 |
| check | 分析レポート+違反一覧 | 検出精度、カバレッジ |
| code | テスト結果+差分 | pass率、coverage |

### 相談メッセージテンプレート

```
【CODEXAPP事前相談】

対象エージェント: {agent_name}
エージェントの役割: {agent_description}
SKILL.md概要: {skill_summary}

以下を決めたい:
1. このエージェントの品質を測るために、どのログをどう取得すべきか？
2. 評価タスクとして何を実行させるのが最適か？
3. エージェント品質スコアの評価基準は？
4. ログ適切性スコアの評価基準は？

回答形式:
- log_strategy: {ログ取得方法の詳細}
- task_spec: {評価タスクの定義}
- quality_criteria: {品質評価基準}
- log_criteria: {ログ適切性評価基準}
```

### 相談結果の保存

```python
# 相談結果を task_spec として保存task_spec = {
    "agent": agent_name,
    "consulted_at": datetime.now().isoformat(),
    "log_strategy": codex_response["log_strategy"],
    "task_definition": codex_response["task_spec"],
    "quality_criteria": codex_response["quality_criteria"],
    "log_criteria": codex_response["log_criteria"],
}
# _temp/codex_review_{agent}_{date}_spec.json に保存```

> [!IMPORTANT]
> **毎回相談する**: 同じエージェントでも、前回のフィードバックを踏まえて評価基準が変わる可能性がある。ラウンド1は必ず相談。ラウンド2以降は前回のtask_specを添えて「これで良いか？変更点は？」と確認する。

## 🔧 CODEXAPPブリッジ

### 起動前提条件

CODEXAPPを使用する前に、CDP（Chrome DevTools Protocol）方式で起動する必要がある。

| ポート | 用途 | 起動方法 |
|:------|:---------|:--------|
| 9222 | ❌ 使用禁止 | Chrome DevToolsデフォルト。競合必至 |
| 9223 | CODEXAPP（手動用） | `Codex-Windows\run.cmd`（`--remote-debugging-port=9223` 自動付与） |
| **9224** | **CODEXAPP（エージェント用）** | `run.cmd -CdpPort 9224`（自動化向き） |
| 9225-9230 | **多重起動用** | `run.cmd -CdpPort 9225` 等で動的割り当て |

> [!CAUTION]
> **ポート9222は絶対に使用しない**。Google Chromeが同ポートを使用するため、必ず9223以降を使用すること。

接続確認:
```powershell
# エージェント用 (9224) の接続確認Invoke-RestMethod -Uri "http://localhost:9224/json"
```

### CdpClient API

```python
# CODEXAPPへの送信（codexapp_cdp_client.py）import sys
sys.path.insert(0, '.agent/workflows/codexapp/scripts')
from codexapp_cdp_client import CdpClient, send_message, poll_response, generate_token

cdp = CdpClient(port=9224)
cdp.connect()                           # 接続
cdp.open_new_thread()                   # 新スレッド
cdp.clear_input()                       # 入力欄クリア
token = generate_token()                # トークン生成
send_message(cdp, message, token)       # 送信
response = poll_response(cdp, token)    # 応答待機
cdp.close()                             # 切断
```

### 送信メッセージ構成（汎用テンプレート）

```
必須セクション:
1. ラウンド番号 + 対象エージェント名 + 評価タスク概要
2. v(N-1)→v(N) の具体的修正一覧
3. サマリーJSON全文（L1）
4. 実行マニフェスト（コマンド, exit_code, 実行時間）
5. 構造化ログ全文（L3）
6. 品質レポートJSON（L2）※あれば
7. 成果物サンプル（上位3件）
8. 回答形式指定:
   - エージェント品質スコア (XX/100)
   - ログ適切性スコア (XX/100)
   - 改善評価（前回比）
   - 残改善点（具体的に）
```

## 📊 自律改善エンジン

### フィードバック→修正の自動判断

```python
# フィードバック分析の疑似コードdef analyze_feedback(feedback, scores):
    issues = extract_issues(feedback)        # 指摘事項を抽出
    categorized = categorize(issues)         # カテゴリ分類
    prioritized = sort_by_impact(categorized) # 影響度順ソート
    # 1ラウンド最大3修正（切り分け容易性のため）
    return prioritized[:3]
```

### よくある減点パターンと対策

| 減点カテゴリ | 原因 | 対策 |
|:------------|:-----|:-----|
| **レイヤー不一致** | L1/L2/L3の値が不一致 | 生成コードの値参照先を単一ソースに統一 |
| **ハードコード値** | 成果物内の数値がメトリクスと乖離 | 実行時メトリクスから動的埋込み |
| **ログ欠損** | フェーズの一部がログに記録されない | WorkflowLogger等で全フェーズ記録 |
| **スコア不透明** | 自己評価スコアの計算根拠が不明 | 多軸計算＋各軸の重み明示 |
| **タイムスタンプ不整合** | ログの時系列が不自然 | 動的閾値: `max(500, events × 15)` |
| **警告未分離** | エラーと警告が混在 | severity別に独立フィールド分離 |
| **trust単軸** | 信頼スコアが1指標のみ | 複合指標化（複数シグナル重み付け） |

### 後退検知と回復

```
後退条件: v(N)スコア < v(N-1)スコア
回復手順:
  1. 差分を特定: v(N-1)→v(N) の変更を全列挙
  2. 原因分類: 「変更が悪影響」 or 「既存問題の表面化」
  3. 判断:
     - 変更が悪影響 → ロールバック + 代替案
     - 既存問題 → 根本修正（前の変更は維持）
```

## ⚠️ 既知の罠

> [!CAUTION]
> - **正直すぎるスコアは逆効果**: 自己評価で低スコアを出すと「なぜ低い？」と質問される
> - **ヘッダのハードコード値**: 成果物冒頭の統計値は必ず実行時メトリクスから動的生成
> - **ログ重複**: logging hookが2重書き込みすることがある（ロガー+手動）
> - **レイヤー不一致のまま送信**: 大幅減点の最大原因。VERIFY未通過で送信しない

## 💡 Rules

- **両軸95点が完了条件**: エージェント品質・ログ適切性の両方
- **ログ整合性最優先**: 不一致のまま送信しない
- **ラウンドごとに差分明示**: v(N-1)→v(N) の変更を具体的に列挙
- **1ラウンド最大3修正**: 変更が多すぎると切り分け困難
- **最大試行**: 15ラウンドで到達しなければユーザーに報告
- **Language**: 日本語で報告
