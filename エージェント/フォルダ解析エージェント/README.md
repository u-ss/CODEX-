# フォルダ解析エージェント

指定フォルダ内の各ファイルを再帰的に解析し、構造と内容を把握するエージェント。

## 機能

- **Python (.py)**: AST解析でクラス/関数/import/行数を抽出
- **Markdown (.md)**: 見出し構造・リンクを抽出
- **JSON/YAML**: トップレベルキー・ネスト深度を解析
- **その他**: メタ情報（サイズ、更新日時）、バイナリ判定

## 使い方

```powershell
python scripts/folder_analyzer.py <フォルダパス> [--output-dir <出力先>] [--exclude <除外パターン>] [--max-depth <深度>]
```

## 出力

- `report.json` — 構造化データ
- `report.md` — Markdownレポート

## ディレクトリ構成

```
フォルダ解析エージェント/
├── README.md           ← このファイル
└── scripts/
    └── folder_analyzer.py  ← メインスクリプト
```

## 関連ファイル

- 論理層: `.agent/workflows/check/sub_agents/folder/SPEC.md`, `GUIDE.md`
- テスト: `tests/test_folder_analyzer.py`
