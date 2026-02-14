# CODEXAPP Review Agent v2.0.0 

**自律改善ループ**: 対象エージェントにタスクを設定し、実行ログをCODEXAPPに送信。フィードバックに基づき修正→再実行を繰り返し、両軸95点以上を達成する。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SPEC.md`を読んでから実行

## 📋 Protocol: 6-Phase Review Loop

```
┌───────────────────────────────────────────────────────────────┐
│  Phase -1: CODEXAPP起動 🔌                                    │
│     → ポート競合チェック（9222/9223/9224）                     │
│     → CDP方式でCODEXAPP起動                                   │
│     → 使用するCODEX（Desktop/AppServer）を確認                │
│     ↓                                                         │
│  Phase 0: TASK DESIGN 📋 + CODEX相談                          │
│     → 対象エージェントを指定                                    │
│     → CODEXAPPに相談: ログ戦略・タスク・評価基準を決定          │
│     → 相談結果をtask_specとして保存                             │
│     ↓                                                         │
│  Phase 1: EXECUTE 🚀                                          │
│     → 対象エージェントを評価タスクで実行                        │
│     → exit code + 出力ファイル確認                             │
│     ↓                                                         │
│  Phase 2: VERIFY 🔍                                           │
│     → 実行ログの整合性チェック                                  │
│     → 成果物内ハードコード値 vs 実メトリクスの乖離検出          │
│     → 不整合あり → Phase 4 (FIX) へジャンプ                   │
│     ↓                                                         │
│  Phase 3: SUBMIT 📤                                           │
│     → CODEXAPPにログ＋成果物を送信                             │
│     → スコア＋フィードバック受領                                │
│     → 両軸 >= 95 → 完了 🎉                                   │
│     → 未達 → フィードバック分析 → Phase 4 へ                  │
│     ↓                                                         │
│  Phase 4: FIX ✏️                                              │
│     → フィードバックから改善ポイント抽出（最大3件/ラウンド）    │
│     → コード修正（/code 7-Phase に準拠）                       │
│     → Phase 1 へ戻る（次ラウンド）                             │
└───────────────────────────────────────────────────────────────┘
```

## 🔧 使用ツール

| Phase | ツール |
|:------|:-------|
| CODEXAPP起動 | `run_command`（ポートチェック・起動） |
| TASK DESIGN | `view_file`, `list_dir`（エージェント構造確認）|
| EXECUTE | `run_command`（スクリプト実行） |
| VERIFY | `view_file`, `grep_search`（ログ整合性検証） |
| SUBMIT | `write_to_file`（送信スクリプト生成）, `run_command`（実行） |
| FIX | `replace_file_content`, `view_file`（コード修正） |

## 📝 Phase詳細

### Phase -1: CODEXAPP起動 🔌

> [!IMPORTANT]
> **Phase 0の前に必ず実施**。CODEXAPPが起動していない状態で相談・送信を行うとエラーになる。

```
Step -1-1: ポート競合チェック
  // turbo
  以下のポートの使用状況を確認:

  powershell:
    Get-NetTCPConnection -LocalPort 9222,9223,9224 -ErrorAction SilentlyContinue |
      Select-Object LocalPort, OwningProcess, State |
      Format-Table -AutoSize

  ポート割り当て:
    ┌────────┬──────────────────────┬──────────────────────────────┐
    │ ポート │ 対象                 │ 備考                         │
    ├────────┼──────────────────────┼──────────────────────────────┤
    │ 9222   │ ❌ 使用禁止          │ Chrome DevToolsデフォルト     │
    │ 9223   │ CODEXAPP（手動用）    │ GUI版（CDP経由）             │
    │ 9224   │ CODEXAPP（エージェント用）│ 自動化向き                   │
    └────────┴──────────────────────┴──────────────────────────────┘

  競合検出時:
    → 9222が使用中: 正常（Chromeが使用中。CODEXでは使わない）
    → 9223が使用中: 手動用CODEXAPPが既に起動中 → Step -1-3へ
    → 9224が使用中: エージェント用CODEXAPPが既に起動中 → Step -1-3へ
    → いずれも空き: Step -1-2へ

Step -1-2: CDP方式でCODEXAPP起動
  エージェント用ポート(9224)で起動:

  ■ Codex Desktop App
    起動場所: Codex-Windows/
    コマンド:
      .\run.cmd -CdpPort 9224
    ※ run.ps1 内で --remote-debugging-port=9224 が自動付与される

    ★ 多重起動（2つ目以降）:
      .\run.cmd -CdpPort 9225
      .\run.cmd -CdpPort 9226 -InstanceId review
    ※ ポートとuserdata/cacheが自動分離される

  ⚠️ 注意事項:
    - Full Autoモード推奨（承認ダイアログ不要）

Step -1-3: CDP接続確認
  // turbo
  起動したCODEXAPPへの接続をテスト:

  エージェント用 (9224):
    powershell:
      try {
        $targets = Invoke-RestMethod -Uri "http://localhost:9224/json"
        Write-Host "✅ CODEXAPP接続OK (ターゲット数: $($targets.Count))"
      } catch {
        Write-Host "❌ CODEXAPP未接続"
      }

  接続失敗時:
    → run.cmd -CdpPort 9224 を再実行
    → それでも失敗: ユーザーに報告

Step -1-4: 使用ポート確定
  デフォルト: ポート9224（エージェント用）
  → task_specの "codex_port" に記録
  → Phase 0 へ進む
```

### Phase 0: TASK DESIGN 📋 + CODEX相談

```
入力:
  - 対象エージェント名（例: research, desktop, check）
  - 目標スコア（デフォルト: 95）

Step 0-1: エージェント構造調査
  1. 対象エージェントの構造を調査:
     - エントリポイント（実行スクリプト）
     - SKILL.md / WORKFLOW.md の確認
     - 既存ログ出力先・形式の把握
  2. タスクIDを生成:
     - 形式: {agent}_{YYYYMMDD}_{short_description}
     - 例: research_20260210_3d_market

Step 0-2: CODEXAPPに事前相談
  ★ CODEXAPP経由で以下を相談して決定する:
  1. このエージェントに最適なログ取得方法は？
  2. 品質を測る評価タスクとして何を実行すべきか？
  3. エージェント品質スコアはどう評価すべきか？
  4. ログ適切性スコアはどう評価すべきか？

  送信内容:
    - エージェント名・役割・SKILL.md概要
    - 既存のログ形式・出力先
    - 過去のラウンド結果（あれば）

Step 0-3: task_spec確定
  CODEXの回答をもとにtask_specを生成・保存:

  task_spec = {
    "agent": "research",
    "entry_point": "scripts/autonomy/run_3d_research.py",
    "goal": "3Dモデリング市場の調査レポート生成",
    "log_strategy": "3層(summary+metadata+JSONL)",  ← CODEXが決定
    "log_dir": "_logs/autonomy/research/{YYYYMMDD}/",
    "output_dir": "_outputs/research/",
    "quality_criteria": {...},  ← CODEXが決定
    "log_criteria": {...},      ← CODEXが決定
    "target_score": 95
  }
  保存先: _temp/codex_review_{agent}_{date}_spec.json
```

> [!IMPORTANT]
> **毎ラウンド確認**: ラウンド1は必ず相談。ラウンド2以降は前回のtask_specを添えて「変更点は？」と確認。CODEXの回答に基づきtask_specを更新する。

### Phase 1: EXECUTE 🚀

```
手順:
  1. py_compile で構文チェック（Pythonの場合）
  2. スクリプト実行: python {entry_point}
  3. exit code == 0 を確認
  4. 出力ファイルの存在を確認:
     - task_specで定義したlog_dir内のファイル
     - task_specで定義したoutput_dir内の成果物
  5. ログファイルを一覧化 → 次フェーズへ渡す

失敗時:
  → エラー内容をtask.mdに記録
  → 構文/依存エラーの場合はPhase 4 (FIX)へ
  → 環境エラーの場合はユーザーに報告
```

### Phase 2: VERIFY 🔍

```
ログ整合性チェック（対象エージェントのログ構造に応じて適用）:

汎用チェック:
  □ exit code == 0
  □ 全必須ログファイルが存在する
  □ ログ内タイムスタンプの論理的整合性
  □ エラーカウント/警告数の一貫性
  □ 成果物内のハードコード値 == ログ内の実数値

多層ログの場合 (L1/L2/L3):
  □ L1集計値 == L2詳細の合計
  □ L2メトリクス == L3イベントから再計算した値
  □ スコア値がL1/L2/L3で一致

不整合発見時:
  → 修正箇所を特定
  → Phase 4 (FIX) へジャンプ
  → CODEXAPPへの送信はスキップ（不整合のまま送ると大幅減点）
```

### Phase 3: SUBMIT 📤

```
// turbo
1. codex_review_v{N}.py を生成:
   - task_specに基づきログ/成果物を自動収集
   - 送信メッセージを構成（SKILL.md のテンプレート参照）
   - CodexBridge経由で送信

2. 結果保存: _temp/codex_review_v{N}_result.json

3. スコア解析:
   - エージェント品質: XX/100
   - ログ適切性: XX/100
   - 両軸 >= 目標スコア → DONE
   - いずれか未達 → フィードバック抽出 → Phase 4

4. ラウンド記録:
   - task.md に Round N のスコアを追記
```

### Phase 4: FIX ✏️

```
フィードバック分析:
  1. 減点カテゴリを特定（SKILL.md のパターン集参照）
  2. 影響度が大きい順に優先度付け
  3. 1ラウンド最大3修正に絞る
  4. 修正対象ファイルを特定
  5. /code 7-Phase に準拠して修正

後退検知:
  v(N)スコア < v(N-1)スコア の場合:
  1. v(N-1)→v(N) の変更差分を全列挙
  2. 原因分類: 変更悪影響 or 既存問題表面化
  3. 変更悪影響 → ロールバック + 代替案
  4. 既存問題 → 根本修正

修正後:
  → 構文チェック
  → Phase 1 へ戻る（次ラウンド）
```

## 📊 完了条件

| 指標 | 閾値 |
|:-----|:-----|
| エージェント品質スコア | >= 目標値（デフォルト95） |
| ログ適切性スコア | >= 目標値（デフォルト95） |
| ログ整合性 | 不整合 0 |
| exit code | 0 |
| ラウンド数 | <= 15 |

## 📁 ファイル構成

```
.agent/workflows/codex-review/
  ├── SKILL.md              ← 技術詳細（本体）
  ├── WORKFLOW.md           ← 実行手順（本ファイル）
  └── examples/
      └── research_agent.md ← Research Agent固有の評価知識

.agent/workflows/codexapp/scripts/
  └── codexapp_cdp_client.py ← CODEXAPP CDPクライアント（送信・応答取得）

_temp/
  ├── codex_review_v{N}.py          ← 送信スクリプト（ラウンドごと）
  └── codex_review_v{N}_result.json ← 応答結果

_logs/autonomy/{agent}/{YYYYMMDD}/  ← エージェント実行ログ
_outputs/{agent}/                   ← エージェント成果物
```

## 🔄 ラウンド管理

各ラウンドの進捗を `task.md` で管理:

```markdown
## Round N [/] 実行中（目標: XX点）
- [/] 改善項目1
- [/] 改善項目2

完了時:
## Round N ✅ (品質XX/適切性YY)
- [x] 改善項目1
- [x] 改善項目2
```

## ⚡ Quick Start

```
/codex-review {agent_name} {target_score}

例:
/codex-review research 95
/codex-review desktop 90
/codex-review check 95

→ Phase 0: タスク設計・エージェント構造分析
→ Phase 1: エージェント実行
→ Phase 2: ログ整合性検証
→ Phase 3: CODEXAPP送信 → スコア受領
→ Phase 4: フィードバック修正
→ (ループ: 目標スコア到達まで)
```

## 💡 Rules

- **Phase 0 必須**: タスク定義なしに実行しない
- **ログ整合性最優先**: 不整合のまま送信しない
- **ラウンドごとに差分明示**: v(N-1)→v(N) の変更を明確に
- **1ラウンド最大3修正**: 変更が多すぎると切り分け困難
- **後退時は原因特定優先**: スコアが下がったら分析→回復
- **最大15ラウンド**: 到達しなければユーザーに報告
- **Language**: 日本語で報告
