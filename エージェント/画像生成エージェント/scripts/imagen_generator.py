"""
imagen_generator.py - GCP Vertex AI Imagen 3 画像生成エージェント

GCPクレジットを使用して、shot_list またはテキストプロンプトから
高品質な画像を自動生成する。

使用例:
    # 単発プロンプトで生成
    python imagen_generator.py prompt "cyberpunk city at night" --count 4

    # shot_list からバッチ生成
    python imagen_generator.py generate --project demo

    # 特定ショットだけ生成
    python imagen_generator.py generate --project demo --shot s01

    # GCP認証確認
    python imagen_generator.py verify-auth
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# プロンプト構築モジュール
from prompt_builder import build_prompt_from_shot, build_prompts_from_shotlist, suggest_aspect_ratio

# エージェントのルートディレクトリ
AGENT_ROOT = Path(__file__).resolve().parent.parent
# プロジェクトルート（antigravity）
PROJECT_ROOT = AGENT_ROOT.parent.parent
# デフォルト設定ファイルパス
DEFAULT_CONFIG_PATH = AGENT_ROOT / "config" / "imagen_config.json"

# Vertex AI SDK（遅延インポート）
ImageGenerationModel = None


def _lazy_import_vertex():
    """Vertex AI SDK を遅延インポートする"""
    global ImageGenerationModel
    if ImageGenerationModel is None:
        try:
            from vertexai.preview.vision_models import ImageGenerationModel as _Model
            ImageGenerationModel = _Model
        except ImportError:
            print("❌ google-cloud-aiplatform がインストールされていません")
            print("   pip install google-cloud-aiplatform Pillow")
            sys.exit(1)


# ============================================================
# 設定管理
# ============================================================
def load_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """
    設定ファイルを読み込む。

    Args:
        config_path: 設定ファイルパス（省略時はデフォルト）

    Returns:
        設定辞書
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# GCP認証
# ============================================================
def verify_gcp_auth(config: Optional[dict] = None) -> dict[str, str]:
    """
    GCP認証の状態を確認する（APIは呼ばない）。

    Args:
        config: 設定辞書

    Returns:
        {"status": "ok"|"error", "message": "..."}
    """
    if config is None:
        config = load_config()

    # Project ID チェック
    project_id = config.get("project_id", "")
    if not project_id or project_id == "YOUR_GCP_PROJECT_ID":
        return {
            "status": "error",
            "message": "❌ project_id が未設定です。config/imagen_config.json を編集してください。"
        }

    # 認証情報チェック
    creds_path = config.get("credentials_path", "")
    env_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if creds_path and Path(creds_path).exists():
        return {
            "status": "ok",
            "message": f"✅ 認証OK（サービスアカウント: {creds_path}）"
        }
    elif env_creds and Path(env_creds).exists():
        return {
            "status": "ok",
            "message": f"✅ 認証OK（環境変数: {env_creds}）"
        }
    elif creds_path and not Path(creds_path).exists():
        return {
            "status": "error",
            "message": f"❌ credentials_path のファイルが存在しません: {creds_path}"
        }
    else:
        # gcloud auth を試す（環境変数もcreds_pathもない場合）
        # ADC（Application Default Credentials）の確認
        adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        # Windows の場合
        adc_path_win = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"

        if adc_path.exists() or adc_path_win.exists():
            return {
                "status": "ok",
                "message": "✅ 認証OK（gcloud ADC）"
            }

        return {
            "status": "error",
            "message": (
                "❌ GCP認証情報が見つかりません。以下のいずれかを設定してください:\n"
                "  1. config/imagen_config.json の credentials_path にサービスアカウントキーのパスを設定\n"
                "  2. 環境変数 GOOGLE_APPLICATION_CREDENTIALS を設定\n"
                "  3. gcloud auth application-default login を実行"
            )
        }


# ============================================================
# Vertex AI 初期化
# ============================================================
def _init_vertex(config: dict) -> None:
    """Vertex AI を初期化する"""
    import vertexai

    # 認証ファイルが指定されていれば環境変数に設定
    creds_path = config.get("credentials_path", "")
    if creds_path and Path(creds_path).exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

    vertexai.init(
        project=config["project_id"],
        location=config["location"]
    )


# ============================================================
# 画像生成
# ============================================================
def generate_from_prompt(
    prompt: str,
    output_dir: str,
    config: Optional[dict] = None,
    aspect_ratio: Optional[str] = None,
    count: Optional[int] = None,
    _model_instance: Any = None,
) -> dict[str, Any]:
    """
    テキストプロンプトから画像を生成する。

    Args:
        prompt: 画像生成プロンプト
        output_dir: 出力ディレクトリ
        config: 設定辞書
        aspect_ratio: アスペクト比（省略時は設定のデフォルト値）
        count: 生成枚数（省略時は設定のデフォルト値）
        _model_instance: テスト用モデルインスタンス

    Returns:
        {"status": "success"|"error", "files": [...], "prompt": "..."}
    """
    if config is None:
        config = load_config()

    defaults = config.get("defaults", {})
    ar = aspect_ratio or defaults.get("aspect_ratio", "16:9")
    num = count or defaults.get("count", 2)

    # 出力ディレクトリ作成
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # モデルの取得
    if _model_instance is not None:
        model = _model_instance
    else:
        _lazy_import_vertex()
        _init_vertex(config)
        model = ImageGenerationModel.from_pretrained(config["model"])

    try:
        # 画像生成
        response = model.generate_images(
            prompt=prompt,
            number_of_images=min(num, 4),  # Imagen 3 は最大4枚
            aspect_ratio=ar,
            safety_filter_level=defaults.get("safety_filter_level", "block_few"),
            person_generation=defaults.get("person_generation", "allow_adult"),
        )

        # 保存
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_files = []
        for i, image in enumerate(response.images):
            filename = out_path / f"imagen_{timestamp}_{i:02d}.png"
            # 画像バイトデータを保存
            with open(filename, "wb") as f:
                f.write(image._image_bytes)
            saved_files.append(str(filename))
            print(f"  ✅ 保存: {filename}")

        return {
            "status": "success",
            "files": saved_files,
            "prompt": prompt,
            "count": len(saved_files),
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"❌ 画像生成エラー: {e}",
            "prompt": prompt,
            "files": [],
        }


def generate_from_shotlist(
    shot_list: Optional[dict] = None,
    shot_list_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    config: Optional[dict] = None,
    style: str = "cinematic",
    target_shot: Optional[str] = None,
    _model_instance: Any = None,
) -> dict[str, Any]:
    """
    shot_list からバッチで画像を生成する。

    Args:
        shot_list: shot_list 辞書（直接指定）
        shot_list_path: shot_list.json のパス
        output_dir: 出力ベースディレクトリ
        config: 設定辞書
        style: スタイル指定
        target_shot: 特定ショットIDのみ生成（省略時は全ショット）
        _model_instance: テスト用モデルインスタンス

    Returns:
        {"status": "success"|"error", "total_shots": N, "total_images": N, "results": [...]}
    """
    if config is None:
        config = load_config()

    # shot_list の読み込み
    if shot_list is None:
        if shot_list_path is None:
            return {"status": "error", "message": "shot_list が指定されていません"}
        with open(shot_list_path, "r", encoding="utf-8") as f:
            shot_list = json.load(f)

    # 出力ディレクトリ
    if output_dir is None:
        output_dir = str(PROJECT_ROOT / config.get("output_base", "_outputs/imagen"))

    # プロンプト構築
    prompts = build_prompts_from_shotlist(shot_list, style=style)

    # 特定ショットのフィルタ
    if target_shot:
        prompts = [p for p in prompts if p["shot_id"] == target_shot]
        if not prompts:
            return {"status": "error", "message": f"ショット '{target_shot}' が見つかりません"}

    results = []
    total_images = 0

    for prompt_info in prompts:
        shot_id = prompt_info["shot_id"]
        prompt = prompt_info["prompt"]
        shot_output = str(Path(output_dir) / shot_id)

        print(f"\n🎨 ショット {shot_id} を生成中...")
        print(f"   プロンプト: {prompt[:80]}...")

        result = generate_from_prompt(
            prompt=prompt,
            output_dir=shot_output,
            config=config,
            _model_instance=_model_instance,
        )
        result["shot_id"] = shot_id
        results.append(result)

        if result["status"] == "success":
            total_images += result.get("count", 0)

    return {
        "status": "success",
        "total_shots": len(prompts),
        "total_images": total_images,
        "results": results,
    }


# ============================================================
# CLI
# ============================================================
def main():
    """CLI エントリーポイント"""
    parser = argparse.ArgumentParser(
        description="🎨 画像生成エージェント - GCP Vertex AI Imagen 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # --- verify-auth ---
    sub_verify = subparsers.add_parser("verify-auth", help="GCP認証を確認")
    sub_verify.add_argument("--config", help="設定ファイルパス")

    # --- prompt ---
    sub_prompt = subparsers.add_parser("prompt", help="テキストプロンプトから画像生成")
    sub_prompt.add_argument("text", help="画像生成プロンプト")
    sub_prompt.add_argument("--count", type=int, default=None, help="生成枚数（1-4）")
    sub_prompt.add_argument("--aspect-ratio", default=None, help="アスペクト比（16:9, 1:1, 9:16等）")
    sub_prompt.add_argument("--output", default=None, help="出力ディレクトリ")
    sub_prompt.add_argument("--style", default="cinematic", help="スタイル（cinematic, anime等）")
    sub_prompt.add_argument("--config", help="設定ファイルパス")

    # --- generate ---
    sub_gen = subparsers.add_parser("generate", help="shot_listからバッチ生成")
    sub_gen.add_argument("--project", required=True, help="プロジェクトスラッグ")
    sub_gen.add_argument("--shot", default=None, help="特定ショットIDのみ")
    sub_gen.add_argument("--style", default="cinematic", help="スタイル")
    sub_gen.add_argument("--output", default=None, help="出力ディレクトリ")
    sub_gen.add_argument("--config", help="設定ファイルパス")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # 設定読み込み
    config = load_config(args.config if hasattr(args, "config") and args.config else None)

    if args.command == "verify-auth":
        result = verify_gcp_auth(config)
        print(result["message"])
        sys.exit(0 if result["status"] == "ok" else 1)

    elif args.command == "prompt":
        # 認証チェック
        auth = verify_gcp_auth(config)
        if auth["status"] != "ok":
            print(auth["message"])
            sys.exit(1)

        # スタイルサフィックスを追加
        from prompt_builder import STYLE_SUFFIXES
        style_suffix = STYLE_SUFFIXES.get(args.style, "")
        full_prompt = f"{args.text}, {style_suffix}" if style_suffix else args.text

        # 出力先
        output = args.output or str(
            PROJECT_ROOT / config.get("output_base", "_outputs/imagen") / "prompt"
        )

        print(f"🎨 画像生成中...\n   プロンプト: {full_prompt[:100]}...")
        result = generate_from_prompt(
            prompt=full_prompt,
            output_dir=output,
            config=config,
            aspect_ratio=args.aspect_ratio,
            count=args.count,
        )

        if result["status"] == "success":
            print(f"\n✅ 完了: {result['count']}枚生成")
            for f in result["files"]:
                print(f"   📁 {f}")
        else:
            print(f"\n{result.get('message', '不明なエラー')}")
            sys.exit(1)

    elif args.command == "generate":
        # 認証チェック
        auth = verify_gcp_auth(config)
        if auth["status"] != "ok":
            print(auth["message"])
            sys.exit(1)

        # shot_list パス解決
        shot_list_candidates = [
            PROJECT_ROOT / "projects" / args.project / "shot_list.directed.json",
            PROJECT_ROOT / "projects" / args.project / "shot_list.json",
        ]
        shot_list_path = None
        for candidate in shot_list_candidates:
            if candidate.exists():
                shot_list_path = str(candidate)
                break

        if not shot_list_path:
            print(f"❌ shot_list が見つかりません: projects/{args.project}/")
            sys.exit(1)

        print(f"📋 shot_list: {shot_list_path}")
        result = generate_from_shotlist(
            shot_list_path=shot_list_path,
            output_dir=args.output,
            config=config,
            style=args.style,
            target_shot=args.shot,
        )

        if result["status"] == "success":
            print(f"\n✅ 完了: {result['total_shots']}ショット / {result['total_images']}枚生成")
        else:
            print(f"\n❌ {result.get('message', '不明なエラー')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
