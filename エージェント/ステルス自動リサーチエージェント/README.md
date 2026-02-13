# ステルス自動リサーチエージェント

既存 `/research` とは独立した、完全新規の研究実行エージェントです。

## 対応ワークフロー

- `.agent/workflows/research/sub_agents/stealth_local/SPEC.md`
- `.agent/workflows/research/sub_agents/stealth_local/GUIDE.md`

## 目的

- Stealth Research Tool v2.1 を収集基盤として利用
- ローカル Ollama を推論基盤として利用
- 研究成果を監査可能な形で保存

## 実行例

```powershell
python エージェント/ステルス自動リサーチエージェント/scripts/stealth_research_local.py --goal "MCPを活用した開発フロー" --focus "実装運用時の失敗パターン"
```

## バッチ例

```powershell
python エージェント/ステルス自動リサーチエージェント/scripts/stealth_research_local.py --jobs-file エージェント/ステルス自動リサーチエージェント/scripts/jobs.example.json
```
