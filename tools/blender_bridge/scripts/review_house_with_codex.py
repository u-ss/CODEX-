"""
review_house_with_codex.py - 6視点レンダリング画像をCODEXにレビューさせる

使い方:
    python tools/blender_bridge/scripts/review_house_with_codex.py \
        --image-dir ag_runs/test_v5 --prefix iter_00

CODEXは codex exec 経由で呼び出し、スコアと改善提案をJSON出力する。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="CODEX House Review")
    p.add_argument("--image-dir", required=True, help="レンダリング画像のディレクトリ")
    p.add_argument("--prefix", default="iter_00", help="画像ファイルのプレフィックス")
    p.add_argument("--model", default="gpt-5.3-codex", help="使用するCODEXモデル")
    p.add_argument("--output", default="", help="結果JSONの出力先")
    return p.parse_args()


def build_review_prompt(image_dir: Path, prefix: str) -> str:
    """6視点画像のパスを含むレビュープロンプトを生成"""
    views = ["front", "back", "left", "right", "oblique", "bird"]
    found = []
    for v in views:
        img = image_dir / f"{prefix}_{v}.png"
        if img.exists():
            found.append(f"- {v}: {img}")

    images_list = "\n".join(found) if found else "(画像が見つかりません)"

    prompt = f"""あなたは建築3Dモデルの品質レビュアーです。
以下の6視点レンダリング画像で戸建て住宅の3Dモデルを評価してください。

レンダリング画像:
{images_list}

評価基準 (各項目10点満点、合計100点):
1. 屋根の形状と配置 (壁との密着度、勾配の自然さ)
2. 外壁のテクスチャと質感 (サイディング感、色彩)
3. 窓とドアのディテール (窓枠、ガラス質感)
4. 基礎と地面の仕上げ
5. 家具の配置と存在感 (内部が見える場合)
6. バルコニー・玄関ポーチの仕上げ
7. 外構 (フェンス、駐車場、アプローチ)
8. 全体のプロポーション
9. ライティングと影の品質
10. 実在する戸建てとの一致度

以下のJSON形式で回答してください:
{{
    "total_score": <0-100の整数>,
    "category_scores": {{
        "roof": <0-10>,
        "walls": <0-10>,
        "windows_doors": <0-10>,
        "foundation": <0-10>,
        "furniture": <0-10>,
        "balcony_porch": <0-10>,
        "exterior": <0-10>,
        "proportion": <0-10>,
        "lighting": <0-10>,
        "realism": <0-10>
    }},
    "improvements": [
        "<改善提案1>",
        "<改善提案2>",
        ...
    ],
    "critical_issues": [
        "<致命的な問題点>"
    ]
}}"""
    return prompt


def run_codex_review(prompt: str, model: str) -> str:
    """codex exec でレビューを実行"""
    import platform
    if platform.system() == "Windows":
        cmd = f'codex exec -m {model} "{prompt}"'
        use_shell = True
    else:
        cmd = ["codex", "exec", "-m", model, prompt]
        use_shell = False

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=300, shell=use_shell
        )
        import re
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', result.stdout)
        output = re.sub(r'\x1b\][^\x07]*\x07', '', output)
        lines = output.split('\n')
        cleaned = [l for l in lines if not l.strip().startswith('OpenAI Codex')]
        return '\n'.join(cleaned).strip()
    except subprocess.TimeoutExpired:
        return '{"error": "timeout"}'
    except Exception as e:
        return f'{{"error": "{e}"}}'


def extract_json(text: str) -> dict:
    """レスポンスからJSON部分を抽出"""
    import re
    # JSON ブロックを探す
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"error": "JSON parse failed", "raw": text}


def main():
    args = parse_args()
    image_dir = Path(args.image_dir).resolve()

    if not image_dir.exists():
        print(f"[ERROR] ディレクトリが存在しません: {image_dir}")
        sys.exit(1)

    prompt = build_review_prompt(image_dir, args.prefix)

    print(f"[AG] CODEXレビュー開始 (model={args.model})")
    print(f"[AG] 画像ディレクトリ: {image_dir}")

    response = run_codex_review(prompt, args.model)
    result = extract_json(response)

    # 出力
    output_path = Path(args.output) if args.output else (image_dir / f"{args.prefix}_codex_review.json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    score = result.get("total_score", "N/A")
    print(f"[AG] スコア: {score}")
    print(f"[AG] レビュー結果: {output_path}")

    if isinstance(score, int) and score >= 95:
        print("[AG] ✅ 95点以上達成！")
    else:
        improvements = result.get("improvements", [])
        if improvements:
            print("[AG] 改善提案:")
            for imp in improvements:
                print(f"  - {imp}")

    return 0 if isinstance(score, int) and score >= 95 else 1


if __name__ == "__main__":
    sys.exit(main())
