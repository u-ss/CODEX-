"""
prompt_builder.py - shot_list からImagen用プロンプトを構築するモジュール

shot_list.json のテキスト情報を、Vertex AI Imagen 3 に最適化された
英語プロンプトに変換する。
"""
from typing import Any


# スタイルごとのサフィックス定義
STYLE_SUFFIXES = {
    "cinematic": "cinematic lighting, dramatic composition, film grain, professional color grading, 8K resolution",
    "anime": "anime style, cel shading, vibrant colors, detailed illustration, studio quality",
    "photorealistic": "photorealistic, ultra detailed, natural lighting, DSLR quality, sharp focus",
    "concept_art": "concept art, digital painting, artstation trending, highly detailed, matte painting",
    "watercolor": "watercolor painting, soft edges, artistic, delicate brush strokes, traditional art",
}

# アスペクト比マッピング
ASPECT_RATIO_MAP = {
    "video": "16:9",
    "landscape": "16:9",
    "portrait": "9:16",
    "vertical": "9:16",
    "square": "1:1",
    "general": "1:1",
    "photo": "4:3",
    "wide": "16:9",
}


def build_prompt_from_shot(
    shot: dict[str, Any],
    style: str = "cinematic",
) -> str:
    """
    1ショットの情報からImagen用プロンプトを構築する。

    Args:
        shot: shot_list の1ショット辞書。
              必須キー: shot_id
              任意キー: text, video.storyboard
        style: スタイル指定（cinematic, anime, photorealistic等）

    Returns:
        Imagen 3 に渡すプロンプト文字列
    """
    parts: list[str] = []

    # storyboard 情報を優先（英語であることが多い）
    video = shot.get("video", {}) or {}
    storyboard = video.get("storyboard", "")
    if storyboard:
        parts.append(storyboard.strip())

    # テキスト情報を追加
    text = shot.get("text", "")
    if text:
        # 日本語テキストはシーン説明として追加
        # NOTE: 将来的にはGemini APIで英訳する拡張が可能
        parts.append(text.strip())

    # パーツが空ならショットIDをフォールバック
    if not parts:
        parts.append(f"scene {shot.get('shot_id', 'unknown')}")

    # スタイルサフィックスを追加
    style_suffix = STYLE_SUFFIXES.get(style.lower(), STYLE_SUFFIXES["cinematic"])
    parts.append(style_suffix)

    return ", ".join(parts)


def build_prompts_from_shotlist(
    shot_list: dict[str, Any],
    style: str = "cinematic",
) -> list[dict[str, str]]:
    """
    shot_list 全体からプロンプト一覧を生成する。

    Args:
        shot_list: shot_list.json の内容（"shots" キーを持つ辞書）
        style: スタイル指定

    Returns:
        [{"shot_id": "s01", "prompt": "..."}, ...] のリスト
    """
    shots = shot_list.get("shots", [])
    results = []
    for shot in shots:
        prompt = build_prompt_from_shot(shot, style=style)
        results.append({
            "shot_id": shot.get("shot_id", "unknown"),
            "prompt": prompt,
        })
    return results


def suggest_aspect_ratio(usage: str = "general") -> str:
    """
    用途に応じたアスペクト比を提案する。

    Args:
        usage: 用途文字列（"video", "portrait", "square" 等）

    Returns:
        アスペクト比文字列（"16:9", "9:16", "1:1" 等）
    """
    return ASPECT_RATIO_MAP.get(usage.lower(), "1:1")
