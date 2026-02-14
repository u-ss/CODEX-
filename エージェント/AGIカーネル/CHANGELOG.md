# AGI Kernel CHANGELOG

## v0.5.1 (2026-02-15)

### バグ修正
- **[P2] report/state 整合性修正**: `paused_now` 判定を report 生成前に移動し、`state.json` と `report.json` の `status` が常に一致するように修正
- **[P2] パス検証強化**: `".." in path_str` → `Path.parts` でコンポーネント単位検出に変更。`startswith()` 文字列比較 → `Path.relative_to()` に変更し、prefix衝突脆弱性を解消
- **[P3] `__version__`**: `0.4.0` → `0.5.1` に更新

### テスト追加
- `TestPathValidationBoundary`: パス検証の境界ケース6件（`..`コンポーネント, ファイル名含み`..`, 絶対パス, prefix衝突, 正常ネスト）
- `TestPausedNowReportConsistency`: paused_now 回帰テスト3件（MAX到達, PAUSED除外, 重複追加防止）
- 合計: 131 → 140 テスト

---

## v0.5.0 (2026-02-14)

### 新機能
- **nodeid分割**: pytest失敗を `nodeid` 単位で候補分割（精密な検証・修正）
- **auto_fixable判定**: `annotate_candidates()` で修正可否を判定。不可候補は `blocked_candidates` に分類
- **select_taskフィルタ**: `auto_fixable=false` 候補は選択対象から除外
- **環境ブロッカー**: preflight失敗は `failure_log` に積まず即 `PAUSED` + exit 1
- **PAUSED即停止**: `record_failure()` が `paused_now=True` を返したら即停止
- **report強化**: `blocked_candidates` + `no_fixable_candidates` reason を追加

### テスト追加
- `TestFailureNodes`: `_extract_failure_nodes` テスト7件
- `TestNodeidSplitting`: `generate_candidates` nodeid分割テスト3件
- `TestAutoFixable`: `annotate_candidates` テスト7件
- `TestSelectAutoFixable`: `select_task` auto_fixableフィルタテスト4件
- `TestRecordFailurePaused`: `record_failure` paused_now戻り値テスト3件
- `TestVerifierNodeid`: `Verifier` target_nodeid対応テスト2件

---

## v0.4.0 以前

初期実装。8フェーズパイプライン、LLMパッチ生成、状態永続化、失敗管理、KI Learning。
